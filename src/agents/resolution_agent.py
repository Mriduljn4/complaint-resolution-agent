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
        regulation: str
    ) -> str:
        """
        Drafts a plain-language, empathetic, legally compliant customer response
        citing the governing regulation and providing next steps. Uses Azure OpenAI when available.
        """
        # 1. Live Azure OpenAI Generation (if connected)
        if self.azure_client:
            try:
                system_prompt = (
                    "You are a regulatory compliance resolution officer for a major US retail bank. "
                    "Draft an empathetic, professional, legally compliant customer response that explicitly cites "
                    f"the governing regulation ({citation}), states the mandatory statutory investigation completion date ({due_date_formatted}), "
                    f"and provides official rights and next steps. "
                    + ("Include the mandatory provisional credit disclosure under Reg E." if "Regulation E" in regulation else "Include the Fair Credit Billing Act withholding right.")
                )
                user_msg = f"Product: {product}\nIssue: {issue}\nStatutory Rule: {citation} ({regulation})\nInvestigation Due Date: {due_date_formatted}\nRule Link: {rule_url}"
                resp = self.azure_client.chat.completions.create(
                    model=self.deployment_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg}
                    ],
                    temperature=0.2,
                    max_tokens=350
                )
                content = resp.choices[0].message.content
                if content and len(content.strip()) > 50:
                    return content.strip()
            except Exception:
                pass  # Fallback to verified template

        # 2. Verified Deterministic Compliance Template
        if "Regulation E" in regulation:
            provisional_credit_clause = (
                "If our investigation requires extended review beyond standard timelines, "
                "we will issue a provisional credit to your account for the disputed amount, "
                "giving you full access to your funds while we finalize the inquiry."
            )
            return (
                f"Dear Valued Customer,\n\n"
                f"Thank you for contacting us regarding the recent issue with your {product.lower()} ({issue}). "
                f"We take concerns regarding unauthorized transactions and account accuracy very seriously.\n\n"
                f"In accordance with federal consumer protection standards under {citation} ({regulation}), "
                f"our compliance team has initiated an immediate investigation. Under these federal rules, "
                f"we are required to complete our investigation and provide you with a formal resolution no later than {due_date_formatted}.\n\n"
                f"{provisional_credit_clause}\n\n"
                f"You have the right under federal law to inspect and request copies of all documentary evidence relied upon during our review. "
                f"You can review the applicable federal requirements at: {rule_url}.\n\n"
                f"If you have additional documentation or questions in the meantime, please reply to this notice quoting your complaint reference. "
                f"We appreciate your patience as we work to resolve this matter promptly.\n\n"
                f"Sincerely,\n"
                f"Consumer Complaints Resolution Department\n"
                f"National Retail Banking Operations"
            )
        else: # Regulation Z
            return (
                f"Dear Valued Cardholder,\n\n"
                f"We have received your billing error notice concerning your {product.lower()} account regarding: {issue}. "
                f"We understand your concern and have formally opened a dispute investigation.\n\n"
                f"Pursuant to the Fair Credit Billing Act and federal regulations under {citation} ({regulation}), "
                f"we are providing this written confirmation of your dispute. Under federal guidelines, our comprehensive investigation "
                f"will be completed and communicated to you within two complete billing cycles, but in no event later than {due_date_formatted}.\n\n"
                f"Important Rights While Your Dispute Is Pending:\n"
                f"- You are not required to pay the disputed portion of your bill or related finance charges while our review is active.\n"
                f"- The disputed amount will not be reported as delinquent to credit reporting bureaus, nor will it impact your credit standing.\n\n"
                f"To review your statutory rights under federal law, please visit: {rule_url}.\n\n"
                f"Thank you for your relationship with our institution. We will notify you in writing the moment our findings are concluded.\n\n"
                f"Sincerely,\n"
                f"Credit Card Dispute & Billing Operations"
            )
