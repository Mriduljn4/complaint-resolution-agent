import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
import yaml
from dotenv import load_dotenv

load_dotenv()

try:
    from openai import AzureOpenAI
    AZURE_OPENAI_AVAILABLE = True
except ImportError:
    AZURE_OPENAI_AVAILABLE = False

CFPB_PRODUCTS = [
    "Checking or savings account",
    "Credit card",
    "Money transfer, virtual currency, or money service",
]

CFPB_ISSUES = {
    "Credit card": [
        "Getting a credit card",
        "Closing your account",
        "Advertising and marketing, including promotional offers",
        "Other features, terms, or problems",
        "Incorrect information on your report",
        "Fees or interest",
        "Problem when making payments",
        "Struggling to pay your bill",
        "Trouble using your card",
        "Problem with a purchase shown on your statement",
    ],
    "Checking or savings account": [
        "Opening an account",
        "Debanking",
        "Closing an account",
        "Problem with a transaction",
        "Problem using a debit or ATM card",
        "Managing an account",
        "Problem caused by your funds being low",
    ],
    "Money transfer, virtual currency, or money service": [
        "Money was not available when promised",
        "Fraud or scam",
        "Foreign currency exchange",
        "Money order, traveler's check or cashier's check",
        "Mobile or digital wallet",
        "Domestic (US) money transfer",
    ],
}


class TriageAgent:
    """
    Reads the customer complaint, picks the right CFPB Product and Issue using gpt-4.1,
    and decides whether Risk is High or Normal based on config/risk_rules.yaml.
    """

    def __init__(self, config_path: str = "config/risk_rules.yaml"):
        self.config_path = config_path
        self.risk_config: Dict[str, Any] = {}
        self._load_config()

        self.azure_client = None
        self.deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1")
        aoai_ep = os.getenv("AZURE_OPENAI_ENDPOINT")
        aoai_key = os.getenv("AZURE_OPENAI_API_KEY")
        if AZURE_OPENAI_AVAILABLE and aoai_ep and aoai_key:
            try:
                self.azure_client = AzureOpenAI(
                    azure_endpoint=aoai_ep,
                    api_key=aoai_key,
                    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
                )
            except Exception:
                self.azure_client = None

    def _load_config(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cfg_path = os.path.join(base_dir, self.config_path) if not os.path.isabs(self.config_path) else self.config_path
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                self.risk_config = yaml.safe_load(f) or {}

    def _classify_with_azure_openai(self, narrative: str) -> Optional[Tuple[str, float, str, float]]:
        """Classify Product and Issue via live Azure OpenAI structured JSON output."""
        if not self.azure_client:
            return None
        try:
            system_prompt = (
                "You are a US banking CFPB complaint classifier. Classify the complaint strictly into one Product "
                f"from {CFPB_PRODUCTS} and the best matching Issue from {json.dumps(CFPB_ISSUES)}.\n"
                "DISAMBIGUATION RULES:\n"
                "- Debit card, ATM, ACH transfer, overdraft, checking/savings -> 'Checking or savings account'.\n"
                "- P2P (Zelle/Venmo), wire transfer, remittance, mobile wallet -> 'Money transfer, virtual currency, or money service'.\n"
                "- Revolving credit card billing/statement/APR/application -> 'Credit card'.\n"
                "ISSUE MAPPING RULES (CFPB top-level Issue taxonomy):\n"
                "- For 'Checking or savings account': unauthorized debit/ATM/ACH transactions, double deductions, overdrafts, and deposit/withdrawal errors -> 'Managing an account'; opening/identity theft on new bank account -> 'Opening an account'; closing/withholding funds/debanking -> 'Closing an account'.\n"
                "- For 'Credit card': billing errors, unauthorized charges, uncredited merchant refunds, APR/fee disputes -> 'Problem with a purchase shown on your statement'; unapplied/fraudulent card opened or application issue -> 'Getting a credit card'; credit bureau reporting errors -> 'Incorrect information on your report'; promotional APR/ads -> 'Advertising and marketing, including promotional offers'; closing card -> 'Closing your account'; other card terms -> 'Other features, terms, or problems'.\n"
                "- For 'Money transfer, virtual currency, or money service': scam/fraud/unauthorized P2P -> 'Fraud or scam'; delayed/stuck transfer -> 'Money was not available when promised'.\n"
                'Respond ONLY as JSON: {"product": "...", "product_confidence": 0.95, "issue": "...", "issue_confidence": 0.90}'
            )
            resp = self.azure_client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": narrative[:1500]},
                ],
                temperature=0.0,
                max_tokens=150,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(resp.choices[0].message.content)
            product = parsed.get("product")
            issue = parsed.get("issue")
            if product in CFPB_PRODUCTS and issue in CFPB_ISSUES.get(product, []):
                return (
                    product,
                    round(float(parsed.get("product_confidence", 0.92)), 2),
                    issue,
                    round(float(parsed.get("issue_confidence", 0.88)), 2),
                )
        except Exception:
            return None
        return None

    def classify_product(self, narrative: str) -> Tuple[str, float]:
        text = narrative.lower()
        scores = {p: 0.0 for p in CFPB_PRODUCTS}

        for kw in ("credit card", "charge card", "cardholder", "billing statement", "billing dispute",
                   "annual fee", "credit limit", "apr", "card was charged", "dispute a purchase", "interest rate"):
            if kw in text:
                scores["Credit card"] += 1.5

        for kw in ("checking account", "savings account", "debit card", "atm", "check deposit", "withdrawal",
                   "overdraft", "ach debit", "teller", "direct deposit", "bank account", "debanked"):
            if kw in text:
                scores["Checking or savings account"] += 1.5

        for kw in ("money transfer", "p2p", "zelle", "venmo", "remittance", "wire transfer",
                   "digital wallet", "sent money", "recipient", "money order", "cashier's check"):
            if kw in text:
                scores["Money transfer, virtual currency, or money service"] += 2.0

        best_product = max(scores, key=scores.get)
        if scores[best_product] == 0.0:
            best_product = "Checking or savings account"
        confidence = min(0.98, max(0.60, 0.5 + (scores[best_product] * 0.1)))
        return best_product, round(confidence, 2)

    def classify_issue(self, narrative: str, product: str) -> Tuple[str, float]:
        text = narrative.lower()
        if product == "Credit card":
            if any(k in text for k in ["card opened without", "never applied", "identity theft", "unauthorized card", "unauthorized account", "fraudulently opened", "new account appeared", "application denied", "delay in processing application", "sent card you never applied"]):
                return "Getting a credit card", 0.95
            if any(k in text for k in ["close", "closing", "paid off my credit card in full"]):
                return "Closing your account", 0.92
            if any(k in text for k in ["introductory apr", "promotional", "advertising", "marketing"]):
                return "Advertising and marketing, including promotional offers", 0.92
            if any(k in text for k in ["add-on", "debt protection", "privacy"]):
                return "Other features, terms, or problems", 0.91
            if any(k in text for k in ["credit report", "delinquent", "credit bureau", "belongs to someone else"]):
                return "Incorrect information on your report", 0.93
            if any(k in text for k in ["fee", "interest", "finance charge", "penalty apr"]):
                return "Fees or interest", 0.90
            if any(k in text for k in ["making payments", "delayed posting", "payment process"]):
                return "Problem when making payments", 0.89
            if any(k in text for k in ["hardship", "struggling to pay"]):
                return "Struggling to pay your bill", 0.88
            if any(k in text for k in ["credit limit", "reduce my credit line"]):
                return "Trouble using your card", 0.88
            return "Problem with a purchase shown on your statement", 0.85

        if product == "Money transfer, virtual currency, or money service":
            if any(k in text for k in ["money was not available", "never arrived", "stuck in pending", "promised"]):
                return "Money was not available when promised", 0.93
            if any(k in text for k in ["scam", "fraud", "impersonation", "stolen credentials", "drained"]):
                return "Fraud or scam", 0.94
            if any(k in text for k in ["exchange rate", "currency exchange", "remittance markup"]):
                return "Foreign currency exchange", 0.92
            if any(k in text for k in ["lost or stolen money order", "cashier's check"]):
                return "Money order, traveler's check or cashier's check", 0.91
            if any(k in text for k in ["digital wallet", "mobile wallet"]):
                return "Mobile or digital wallet", 0.90
            return "Domestic (US) money transfer", 0.85

        if any(k in text for k in ["open an account", "opening an account", "attempted to open", "unable to open", "account was opened in my name"]):
            return "Opening an account", 0.94
        if any(k in text for k in ["debanked", "terminated without cause", "frozen without cause"]):
            return "Debanking", 0.95
        if any(k in text for k in ["closed my checking", "withholding my remaining balance", "can't close", "closing an account", "closing balance"]):
            return "Closing an account", 0.93
        if any(k in text for k in ["transfer i didn't make", "transfer i did not make", "transfer i didnt make", "unauthorized transfer", "taken from my account", "problem with a transaction"]):
            return "Problem with a transaction", 0.94
        if any(k in text for k in ["unauthorized", "stolen", "not authorized", "fraudulent charge", "debit card number"]):
            if "atm" in text or "debit" in text:
                return "Problem using a debit or ATM card", 0.93
            return "Managing an account", 0.90
        return "Managing an account", 0.85

    def evaluate_risk(
        self,
        narrative: str,
        product: str,
        issue: str,
        disputed_amount: Optional[float] = None,
        tags: Optional[str] = None,
    ) -> Tuple[str, List[str]]:
        text = narrative.lower()
        rules = self.risk_config.get("rules", {})
        reasons = []

        amt = disputed_amount
        if amt is None:
            amt_match = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)", narrative)
            if amt_match:
                try:
                    amt = float(amt_match.group(1).replace(",", ""))
                except ValueError:
                    amt = 0.0

        thresh = rules.get("monetary_threshold", {}).get("amount_usd", 1000.0)
        if amt is not None and amt >= thresh:
            reasons.append(f"Disputed amount ${amt:,.2f} exceeds high-risk threshold (${thresh:,.2f})")

        id_rule = rules.get("identity_theft_unauthorized_account", {})
        if issue in id_rule.get("trigger_issues", []) or any(kw in text for kw in id_rule.get("trigger_keywords", [])):
            reasons.append(id_rule.get("reason", "Suspected identity theft or fraudulent account origin"))

        debank_rule = rules.get("debanking_fund_deprivation", {})
        if issue in debank_rule.get("trigger_issues", []) or any(kw in text for kw in debank_rule.get("trigger_keywords", [])):
            reasons.append(debank_rule.get("reason", "Account freeze, debanking, or severe deprivation of essential consumer funds"))

        scam_rule = rules.get("scam_wire_drain", {})
        if (
            issue in scam_rule.get("trigger_issues", [])
            or issue == "Problem with a transaction"
            or any(kw in text for kw in scam_rule.get("trigger_keywords", []))
            or any(kw in text for kw in ["transfer i didn't make", "transfer i did not make", "transfer i didnt make", "unauthorized transfer", "taken from my account"])
        ):
            reasons.append("High (possible unauthorized transfer or electronic fund dispute)")

        vuln_rule = rules.get("vulnerable_consumer", {})
        if tags:
            for vtag in vuln_rule.get("trigger_tags", []):
                if vtag.lower() in tags.lower() and amt is not None and amt >= vuln_rule.get("amount_threshold", 500.0):
                    reasons.append(f"Protected consumer group ({vtag}) with dispute over ${vuln_rule.get('amount_threshold', 500.0):,.2f}")

        if reasons:
            return "High", reasons
        return "Normal", ["Standard operational inquiry within normal handling parameters"]

    def triage_complaint(
        self,
        narrative: str,
        disputed_amount: Optional[float] = None,
        tags: Optional[str] = None,
        provided_product: Optional[str] = None,
        provided_issue: Optional[str] = None,
    ) -> Dict[str, Any]:
        llm_res = None if (provided_product and provided_issue) else self._classify_with_azure_openai(narrative)
        if llm_res:
            pred_product, p_conf, pred_issue, i_conf = llm_res
            provider = f"Azure OpenAI ({self.deployment_name})"
        else:
            pred_product, p_conf = self.classify_product(narrative)
            pred_issue, i_conf = self.classify_issue(narrative, pred_product)
            provider = "Local Heuristic Fallback"

        product = provided_product or pred_product
        issue = provided_issue or pred_issue
        risk_level, risk_reasons = self.evaluate_risk(
            narrative=narrative,
            product=product,
            issue=issue,
            disputed_amount=disputed_amount,
            tags=tags,
        )

        return {
            "product": product,
            "product_confidence": p_conf,
            "issue": issue,
            "issue_confidence": i_conf,
            "risk_level": risk_level,
            "risk_reasons": risk_reasons,
            "disputed_amount": disputed_amount,
            "triage_provider": provider,
            "triage_summary": f"Product: {product} | Issue: {issue} | Risk: {risk_level} ({'; '.join(risk_reasons)})",
        }
