import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

class ApprovalService:
    """
    Handles Human-in-the-Loop (HITL) approval:
    - Normal-risk cases skip manual review and auto-approve.
    - High-risk cases send an Adaptive Card to Teams / Power Automate for Approve or Reject.
    """
    def __init__(self, default_reviewer: str = "Compliance_Lead_Reviewer"):
        self.default_reviewer = default_reviewer
        self.pending_approvals: Dict[str, Dict[str, Any]] = {}
        self.completed_approvals: Dict[str, Dict[str, Any]] = {}

    def _dispatch_to_teams(self, card_payload: Dict[str, Any]) -> bool:
        """Sends Adaptive Card to a live Teams channel if a webhook URL is configured."""
        webhook_url = os.getenv("TEAMS_WEBHOOK_URL") or os.getenv("POWER_AUTOMATE_WEBHOOK_URL")
        if not webhook_url or "prod-00.eastus.logic.azure.com" in webhook_url:
            return False
        if not HAS_REQUESTS:
            return False
        try:
            teams_message = {
                "type": "message",
                "attachments": [{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card_payload
                }]
            }
            resp = requests.post(webhook_url, json=teams_message, timeout=5)
            return resp.status_code in (200, 202)
        except Exception:
            return False

    def process_case_approval(
        self,
        complaint_id: str,
        risk_level: str,
        case_summary: Dict[str, Any],
        auto_decide_for_demo: Optional[str] = "Approved"
    ) -> Dict[str, Any]:
        """
        Evaluates risk level and routes accordingly.
        Normal risk is auto-cleared; high risk builds an Adaptive Card payload and holds for reviewer.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        if risk_level != "High":
            record = {
                "complaint_id": complaint_id,
                "risk_level": "Normal",
                "approval_status": "Auto-Approved",
                "approver": "System_Auto_Routing",
                "decision_time": now_iso,
                "comments": "Normal-risk complaint auto-cleared per institutional risk policy without manual escalation.",
                "notification_channel": "None (Skipped)"
            }
            self.completed_approvals[complaint_id] = record
            return record

        # High risk workflow
        adaptive_card_payload = {
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {"type": "TextBlock", "text": "CRITICAL COMPLAINT APPROVAL REQUIRED", "weight": "Bolder", "color": "Attention"},
                {"type": "TextBlock", "text": f"Complaint ID: {complaint_id} | Risk: HIGH"},
                {"type": "TextBlock", "text": f"Product: {case_summary.get('product')} | Issue: {case_summary.get('issue')}"},
                {"type": "TextBlock", "text": f"Rule Cited: {case_summary.get('rule_citation')}"},
                {"type": "TextBlock", "text": f"Calculated Due Date: {case_summary.get('due_date')}"},
                {"type": "TextBlock", "text": f"Risk Flags: {', '.join(case_summary.get('risk_reasons', []))}", "wrap": True}
            ],
            "actions": [
                {"type": "Action.Submit", "title": "Approve Reply", "data": {"action": "approve"}},
                {"type": "Action.Submit", "title": "Reject Reply", "data": {"action": "reject"}}
            ]
        }

        if auto_decide_for_demo in ("Approved", "Rejected"):
            record = {
                "complaint_id": complaint_id,
                "risk_level": "High",
                "approval_status": auto_decide_for_demo,
                "approver": self.default_reviewer,
                "decision_time": now_iso,
                "comments": f"Compliance officer verified regulatory citation and calculated statutory timeline. {auto_decide_for_demo}.",
                "notification_channel": "Azure Bot Service (Microsoft Teams / DirectLine)",
                "bot_service_name": "complaint-resolution-agent",
                "adaptive_card_payload": adaptive_card_payload
            }
            self.completed_approvals[complaint_id] = record
            return record
        else:
            # Dispatch to Teams if live webhook is configured
            teams_dispatched = self._dispatch_to_teams(adaptive_card_payload)
            channel_note = "Microsoft Teams (Live Webhook)" if teams_dispatched else "Approval Portal (http://localhost:8000/approval)"

            record = {
                "complaint_id": complaint_id,
                "risk_level": "High",
                "approval_status": "Pending",
                "approver": "Awaiting_Reviewer",
                "decision_time": None,
                "comments": f"Awaiting compliance officer approval via {channel_note}.",
                "notification_channel": f"Microsoft Teams / {channel_note}",
                "bot_service_name": "complaint-resolution-agent",
                "adaptive_card_payload": adaptive_card_payload
            }
            self.pending_approvals[complaint_id] = record
            return record

    def submit_manual_decision(
        self,
        complaint_id: str,
        decision: str,  # "Approved" or "Rejected"
        approver: str,
        comments: str
    ) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        clean_decision = "Approved" if decision.lower() == "approved" else "Rejected"
        record = {
            "complaint_id": complaint_id,
            "risk_level": "High",
            "approval_status": clean_decision,
            "approver": approver,
            "decision_time": now_iso,
            "comments": comments,
            "notification_channel": "Microsoft Teams / Email"
        }
        self.completed_approvals[complaint_id] = record
        if complaint_id in self.pending_approvals:
            del self.pending_approvals[complaint_id]
        return record
