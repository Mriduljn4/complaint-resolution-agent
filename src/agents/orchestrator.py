import os
import uuid
from typing import Dict, Any, Optional

from src.guardrails.input_guardrail import InputGuardrail
from src.knowledge_base import RegulatoryKnowledgeBase
from src.deadline_tool import DeadlineTool
from src.agents.triage_agent import TriageAgent
from src.agents.resolution_agent import ResolutionAgent
from src.approval.approval_service import ApprovalService
from src.telemetry.audit_logger import AuditLogger
from src.telemetry.tracing import PipelineTracer

class ComplaintCopilotOrchestrator:
    """
    Main pipeline coordinator that connects all steps in sequence:
    Guardrail -> Triage -> Azure AI Search -> Deadline Tool -> Draft Reply -> Approval -> Audit Log.
    """
    def __init__(
        self,
        knowledge_base: Optional[RegulatoryKnowledgeBase] = None,
        deadline_tool: Optional[DeadlineTool] = None,
        audit_logger: Optional[AuditLogger] = None
    ):
        self.guardrail = InputGuardrail()
        self.kb = knowledge_base or RegulatoryKnowledgeBase()
        self.deadline_tool = deadline_tool or DeadlineTool()
        self.triage_agent = TriageAgent()
        self.resolution_agent = ResolutionAgent(knowledge_base=self.kb, deadline_tool=self.deadline_tool)
        self.approval_service = ApprovalService()
        self.audit_logger = audit_logger or AuditLogger()
        self.model_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1")
        self.prompt_version = "v1.2.0"

    def process_complaint(
        self,
        complaint_dict: Dict[str, Any],
        auto_approve_high_risk: bool = True
    ) -> Dict[str, Any]:
        """
        Processes a single customer complaint through all pipeline stages.
        Returns a structured case record ready for customer reply and audit trail.
        """
        complaint_id = str(complaint_dict.get("Complaint ID", uuid.uuid4().hex[:8]))
        raw_narrative = complaint_dict.get("consumer_complaint_narrative") or complaint_dict.get("narrative") or ""
        error_reported_date = complaint_dict.get("error_reported_date", "2026-11-20")
        is_new_account = bool(complaint_dict.get("is_new_account", False))
        transaction_type = complaint_dict.get("transaction_type")
        disputed_amount = complaint_dict.get("disputed_amount")
        tags = complaint_dict.get("Tags")

        tracer = PipelineTracer(complaint_id=complaint_id)

        # Sanitize incoming narrative
        tracer.start_span("input_guardrail", {"complaint_id": complaint_id})
        guardrail_result = self.guardrail.inspect_and_filter(raw_narrative)
        safe_narrative = guardrail_result["sanitized_narrative"]
        tracer.end_span(
            status="SUCCESS" if guardrail_result["is_safe"] else "THREAT_NEUTRALIZED",
            output={"is_safe": guardrail_result["is_safe"], "threats": guardrail_result["flagged_threats"]}
        )

        # Categorize product/issue and risk level
        tracer.start_span("triage_agent", {"model": self.model_name, "version": self.prompt_version})
        triage_res = self.triage_agent.triage_complaint(
            narrative=safe_narrative,
            disputed_amount=disputed_amount,
            tags=tags
        )
        tracer.end_span(
            status="SUCCESS",
            output={"product": triage_res["product"], "issue": triage_res["issue"], "risk": triage_res["risk_level"]}
        )

        # Retrieve applicable regulation, calculate due date, and draft reply
        resolution_res = self.resolution_agent.resolve(
            narrative=safe_narrative,
            triage_data=triage_res,
            error_reported_date=error_reported_date,
            is_new_account=is_new_account,
            transaction_type=transaction_type,
            tracer=tracer
        )

        # Human-in-the-loop approval routing for high risk cases
        tracer.start_span("approval_flow", {"risk_level": triage_res["risk_level"]})
        approval_res = self.approval_service.process_case_approval(
            complaint_id=complaint_id,
            risk_level=triage_res["risk_level"],
            case_summary={
                "product": triage_res["product"],
                "issue": triage_res["issue"],
                "rule_citation": resolution_res["rule_citation"],
                "due_date": resolution_res["deadline"]["due_date_formatted"],
                "risk_reasons": triage_res["risk_reasons"]
            },
            auto_decide_for_demo="Approved" if auto_approve_high_risk else None
        )
        tracer.end_span(
            status="SUCCESS",
            output={"decision": approval_res["approval_status"], "approver": approval_res["approver"]}
        )

        # Persist audit log entry
        execution_trace = tracer.get_full_trace()
        audit_entry = self.audit_logger.log_case(
            complaint_id=complaint_id,
            rule_cited=resolution_res["rule_citation"],
            due_date=resolution_res["deadline"]["due_date"],
            decision=approval_res["approval_status"],
            approver=approval_res["approver"],
            model=self.model_name,
            prompt_version=self.prompt_version,
            risk_level=triage_res["risk_level"],
            product=triage_res["product"],
            issue=triage_res["issue"],
            execution_trace=execution_trace
        )

        # Package case summary
        case_record = {
            "case_id": complaint_id,
            "complaint_input": {
                "narrative": raw_narrative,
                "sanitized_narrative": safe_narrative,
                "error_reported_date": error_reported_date,
                "is_new_account": is_new_account,
                "transaction_type": transaction_type,
                "disputed_amount": disputed_amount,
                "tags": tags,
                "company": complaint_dict.get("Company", "Financial Institution"),
                "guardrail": guardrail_result
            },
            "triage": {
                "product": triage_res["product"],
                "issue": triage_res["issue"],
                "risk_level": triage_res["risk_level"],
                "risk_reasons": triage_res["risk_reasons"],
                "confidence": {
                    "product": triage_res["product_confidence"],
                    "issue": triage_res["issue_confidence"]
                },
                "summary": triage_res["triage_summary"]
            },
            "rule": {
                "rule_id": resolution_res["rule_id"],
                "citation": resolution_res["rule_citation"],
                "title": resolution_res["rule_title"],
                "url": resolution_res["rule_url"],
                "search_provider": resolution_res.get("search_provider", "Azure AI Search (complaint-regulations-rag-v2)"),
                "governing_regulation": resolution_res["governing_regulation"],
                "status": resolution_res["status"]
            },
            "deadline": {
                "due_date": resolution_res["deadline"]["due_date"],
                "due_date_formatted": resolution_res["deadline"]["due_date_formatted"],
                "days_allocated": resolution_res["deadline"]["days_allocated"],
                "day_type": resolution_res["deadline"]["day_type"],
                "working": resolution_res["deadline"]["working_summary"],
                "details": resolution_res["deadline"].get("calculation_details", {})
            },
            "draft_reply": resolution_res["draft_reply"],
            "approval": {
                "decision": approval_res["approval_status"],
                "approver": approval_res["approver"],
                "timestamp": approval_res["decision_time"],
                "comments": approval_res["comments"],
                "channel": approval_res["notification_channel"],
                "adaptive_card": approval_res.get("adaptive_card_payload")
            },
            "audit_log": audit_entry,
            "execution_trace": execution_trace,
            "total_latency_ms": tracer.get_total_latency_ms()
        }

        return case_record
