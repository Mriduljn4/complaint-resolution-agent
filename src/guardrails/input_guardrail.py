import os
import re
from typing import Any, Dict, List, Tuple

try:
    from azure.ai.contentsafety import ContentSafetyClient
    from azure.ai.contentsafety.models import AnalyzeTextOptions
    from azure.core.credentials import AzureKeyCredential
    HAS_AZURE_CS = True
except ImportError:
    HAS_AZURE_CS = False


class InputGuardrail:
    """
    First-level safety check before calling the AI model.
    1) Calls Azure AI Content Safety for harmful content.
    2) Catches and cleans prompt-injection / jailbreak tricks.
    3) Hides sensitive PII (SSN, card number, email, phone) before LLM inference.
    """

    PATTERNS: List[Tuple[str, re.Pattern]] = [
        (
            "DIRECTIVE_OVERRIDE",
            re.compile(r"(ignore|ignoring|disregard|disregarding|forget|forgot|forgetting|override|overriding|bypass|bypassing)\s+(all\s+)?(previous\s+|your\s+|the\s+|my\s+)?(rules|instructions|directives|guidelines|policy|everything)", re.I),
        ),
        (
            "UNAUTHORIZED_REFUND_PROMISE",
            re.compile(r"(promise\s+(a\s+|me\s+)?refund|immediately\s+credit|guarantee\s+(a\s+)?full\s+refund|pay\s+me\s+immediately|confirm\s+full\s+settlement)", re.I),
        ),
        (
            "ROLE_JAILBREAK",
            re.compile(r"(you\s+are\s+now|act\s+as\s+a|pretend\s+to\s+be|developer\s+mode|dan\s+mode|jailbreak)", re.I),
        ),
        (
            "SYSTEM_DELIMITER_SPOOF",
            re.compile(r"(\[system(\s+instruction|\s+override)?\]|<system_override>|###\s*system|```system)", re.I),
        ),
    ]

    PII_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
        ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
        ("CARD_OR_ACCOUNT", re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "[REDACTED_CARD]"),
        ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
        ("PHONE", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"), "[REDACTED_PHONE]"),
    ]

    def __init__(self):
        self.cs_client = None
        endpoint = os.getenv("AZURE_CONTENT_SAFETY_ENDPOINT")
        key = os.getenv("AZURE_CONTENT_SAFETY_KEY")
        if HAS_AZURE_CS and endpoint and key:
            try:
                self.cs_client = ContentSafetyClient(endpoint, AzureKeyCredential(key))
            except Exception:
                self.cs_client = None

    def inspect_and_filter(self, narrative: str) -> Dict[str, Any]:
        """Check narrative text for safety issues and strip known injection phrases."""
        threats: List[str] = []
        clean_text = narrative
        azure_result = {}

        if self.cs_client:
            try:
                resp = self.cs_client.analyze_text(AnalyzeTextOptions(text=narrative[:1000]))
                for cat in resp.categories_analysis:
                    azure_result[cat.category] = cat.severity
                    if cat.severity >= 2:
                        threats.append(f"AZURE_CONTENT_SAFETY_{cat.category.upper()}")
            except Exception as err:
                azure_result = {"error": str(err)}

        for name, pattern in self.PATTERNS:
            if pattern.search(narrative):
                threats.append(name)
                clean_text = pattern.sub("[FILTERED_ADVERSARIAL_INJECTION]", clean_text)

        redacted_pii: List[str] = []
        for pii_name, pii_pattern, replacement in self.PII_PATTERNS:
            if pii_pattern.search(clean_text):
                redacted_pii.append(pii_name)
                clean_text = pii_pattern.sub(replacement, clean_text)

        is_safe = len(threats) == 0

        return {
            "is_safe": is_safe,
            "flagged_threats": threats,
            "redacted_pii": redacted_pii,
            "original_narrative": narrative,
            "sanitized_narrative": clean_text.strip(),
            "guardrail_action": "PERMITTED" if (is_safe and not redacted_pii) else ("NEUTRALIZED_AND_FLAGGED" if not is_safe else "PII_REDACTED"),
            "azure_content_safety": azure_result,
            "explanation": "Clean" if (is_safe and not redacted_pii) else f"Threats: {threats} | PII Redacted: {redacted_pii}",
        }
