import os
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from src.knowledge_base import RegulatoryKnowledgeBase
from src.deadline_tool import DeadlineTool

load_dotenv()

try:
    from openai import AzureOpenAI
    AZURE_OPENAI_AVAILABLE = True
except ImportError:
    AZURE_OPENAI_AVAILABLE = False

class ResolutionAgent:
    """
    Finds the right rule from Azure AI Search, calls DeadlineTool for the due date,
    and drafts a polite customer reply with proper citation.
    If the query is outside Reg E / Reg Z scope, it returns 'Needs specialist review'.
    """
    def __init__(
        self,
        knowledge_base: Optional[RegulatoryKnowledgeBase] = None,
        deadline_tool: Optional[DeadlineTool] = None
    ):
        self.kb = knowledge_base or RegulatoryKnowledgeBase()
        self.deadline_tool = deadline_tool or DeadlineTool()
        
        self.azure_client = None
        aoai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        aoai_key = os.getenv("AZURE_OPENAI_API_KEY")
        self.deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1")
        if AZURE_OPENAI_AVAILABLE and aoai_endpoint and aoai_key:
            try:
                self.azure_client = AzureOpenAI(
                    azure_endpoint=aoai_endpoint,
                    api_key=aoai_key,
                    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
                )
            except Exception:
                self.azure_client = None

    def resolve(
        self,
        narrative: str,
        triage_data: Dict[str, Any],
        error_reported_date: str,
        is_new_account: bool = False,
        transaction_type: Optional[str] = None,
        tracer: Optional[Any] = None
    ) -> Dict[str, Any]:
        product = triage_data.get("product", "")
        issue = triage_data.get("issue", "")
        risk_level = triage_data.get("risk_level", "Normal")

        # Query regulations index
        search_query = f"{issue} {narrative[:150]}"
        if is_new_account:
            search_query += " new account extension 20 business days"
        else:
            search_query += " standard investigation 10 business days"

        if tracer:
            tracer.start_span("regulatory_search", {"query": search_query, "product": product})

        retrieved_chunks = self.kb.search_regulation(
            query=search_query,
            product=product,
            top_k=2
        )

        if not retrieved_chunks:
            if tracer:
                tracer.end_span(status="FAILED", error="No matching consumer protection regulation found.")
            return self._needs_specialist_review("No matching consumer protection regulation found.")

        top_chunk = retrieved_chunks[0]
        rule_id = top_chunk["id"]
        citation = top_chunk["citation"]
        rule_url = top_chunk["url"]

        if tracer:
            tracer.end_span(
                status="SUCCESS",
                output={"rule_id": rule_id, "citation": citation, "provider": top_chunk.get("search_provider", "Azure AI Search")}
            )

        # Strictly enforce statutory error-resolution scope (Reg E § 1005.11 & Reg Z § 1026.13)
        non_rule_issues = {
            "Advertising and marketing, including promotional offers",
            "Struggling to pay your bill",
            "Trouble using your card",
            "Other features, terms, or problems",
            "Getting a credit card",
            "Closing your account",
            "Closing an account",
            "Incorrect information on your report",
            "Foreign currency exchange",
        }
        out_of_scope_topics = (
            "marketing email", "promotional", "unsubscribe", "spam",
            "mortgage", "student loan", "auto loan", "car loan",
            "credit limit", "annual fee", "reward points", "apr increase",
        )
        error_signals = (
            "unauthorized", "never authorized", "fraud", "stolen",
            "error", "dispute", "not delivered", "never delivered",
            "double", "duplicate", "wrong amount", "did not make", "didn't make",
        )
        text_lower = narrative.lower()
        has_error_signal = any(k in text_lower for k in error_signals)

        if (issue in non_rule_issues and not has_error_signal) or (
            any(t in text_lower for t in out_of_scope_topics) and not has_error_signal
        ):
            return self._needs_specialist_review(
                reason=f"Inquiry ('{issue}') is outside Reg E (12 CFR § 1005.11) and Reg Z (12 CFR § 1026.13) statutory error-resolution scope."
            )

        # Calculate statutory deadline
        if tracer:
            tracer.start_span("deadline_tool", {"rule_id": rule_id, "start_date": error_reported_date, "is_new_account": is_new_account})

        deadline_result = self.deadline_tool.calculate_deadline(
            rule_id=rule_id,
            error_reported_date=error_reported_date,
            is_new_account=is_new_account,
            transaction_type=transaction_type
        )

        due_date = deadline_result["due_date"]
        due_date_formatted = deadline_result["due_date_formatted"]
        working_explanation = deadline_result["calculation_working"]["explanation"]

        if tracer:
            tracer.end_span(
                status="SUCCESS",
                output={"due_date": due_date, "day_type": deadline_result["day_type"], "working": working_explanation}
            )

        # Generate response letter
        if tracer:
            tracer.start_span("reply_generation", {"model": getattr(self, "deployment_name", "gpt-4.1"), "citation": citation})

        draft_reply = self._generate_draft_reply(
            product=product,
            issue=issue,
            citation=citation,
            rule_url=rule_url,
            due_date_formatted=due_date_formatted,
            is_new_account=is_new_account,
            risk_level=risk_level,
            regulation=deadline_result["regulation"]
        )

        if tracer:
            tracer.end_span(
                status="SUCCESS",
                output={"length_chars": len(draft_reply), "due_date_cited": due_date_formatted in draft_reply}
            )

        return {
            "status": "RULE_IDENTIFIED",
            "rule_id": rule_id,
            "rule_citation": citation,
            "rule_title": top_chunk["title"],
            "rule_url": rule_url,
            "search_provider": top_chunk.get("search_provider", "Azure AI Search (complaint-regulations-rag-v2)"),
            "governing_regulation": deadline_result["regulation"],
            "deadline": {
                "due_date": due_date,
                "due_date_formatted": due_date_formatted,
                "days_allocated": deadline_result["days_allocated"],
                "day_type": deadline_result["day_type"],
                "working_summary": working_explanation,
                "calculation_details": deadline_result["calculation_working"]
            },
            "draft_reply": draft_reply,
            "retrieved_context": retrieved_chunks
        }

    def _needs_specialist_review(self, reason: str) -> Dict[str, Any]:
        return {
            "status": "NEEDS_SPECIALIST_REVIEW",
            "rule_id": "NONE",
            "rule_citation": "None (Out of Scope)",
            "rule_title": "Needs specialist review",
            "rule_url": "",
            "governing_regulation": "N/A",
            "deadline": {
                "due_date": "N/A",
                "due_date_formatted": "N/A",
                "days_allocated": 0,
                "day_type": "N/A",
                "working_summary": "No statutory calculation applicable."
            },
            "draft_reply": f"Needs specialist review. {reason} Case has been flagged for legal and compliance specialist escalation.",
            "specialist_review_reason": reason
        }

    def _generate_draft_reply(
        self,
        product: str,
        issue: str,
        citation: str,
        rule_url: str,
        due_date_formatted: str,
        is_new_account: bool,
        risk_level: str,
        regulation: str,
    ) -> str:
        """Draft a plain-text customer reply citing the exact rule and due date."""
        if self.azure_client:
            try:
                right_instruction = (
                    "Explain that if the investigation takes longer than 10 business days, provisional credit for the disputed amount may be provided while the review continues (up to 45 calendar days, or 90 calendar days for new accounts, point-of-sale, or foreign transactions)."
                    if "Regulation E" in regulation
                    else "Explain that under Regulation Z, the customer is not required to pay the disputed amount while the billing dispute is under investigation, and it will not be reported as delinquent."
                )
                system_prompt = (
                    "You are a dispute resolution specialist. Write a clear, polite, well-structured plain-text email reply (no markdown **bold**). "
                    "Start with a 'Subject:' line, greet with 'Dear Valued Customer,', and sign off with 'Sincerely,\nCustomer Dispute Resolution Team'. "
                    "Do NOT use any personal names, specific bank names, or bracketed placeholders like [Your Name] or [Bank Name]. "
                    f"Cite {citation} ({regulation}), state the exact investigation due date ({due_date_formatted}), "
                    f"{right_instruction} Invite the customer to share any supporting documents, and include the official rule URL."
                )
                user_msg = (
                    f"Product: {product}\nIssue: {issue}\nRule: {citation} ({regulation})\n"
                    f"Due Date: {due_date_formatted}\nRule Link: {rule_url}"
                )
                resp = self.azure_client.chat.completions.create(
                    model=self.deployment_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.2,
                    max_tokens=400,
                )
                content = (resp.choices[0].message.content or "").strip()
                if len(content) > 50:
                    return content
            except Exception:
                pass

        right_note = (
            "If our investigation requires more than 10 business days to complete, you may be eligible for provisional credit for the disputed amount while we finish our review."
            if "Regulation E" in regulation
            else "While this dispute is under investigation, you are not required to pay the disputed amount, and it will not be reported as late."
        )
        return (
            f"Subject: Update on Your {product} Dispute\n\n"
            f"Dear Valued Customer,\n\n"
            f"Thank you for contacting us regarding your {product.lower()} dispute ({issue}). We have logged your case and started our review.\n\n"
            f"Under {citation} ({regulation}), we will investigate your claim and share our findings with you by {due_date_formatted}.\n\n"
            f"{right_note} If you have any supporting documents (such as receipts or merchant messages), please share them to assist with our review.\n\n"
            f"Official rule reference: {rule_url}\n\n"
            f"Sincerely,\nCustomer Dispute Resolution Team"
        )
