import json
import os
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

load_dotenv()


class RegulatoryKnowledgeBase:
    """
    Fetches US banking rules (Reg E 12 CFR § 1005.11 and Reg Z 12 CFR § 1026.13)
    from live Azure AI Search (`banking-regulations` index) after locking the product
    using our product_rule_map.json lookup table.
    """

    def __init__(self, mapping_path: str = "data/regulations/product_rule_map.json"):
        self.mapping_path = mapping_path
        self.product_map: Dict[str, Dict[str, Any]] = {}

        self.azure_search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        self.azure_search_key = os.getenv("AZURE_SEARCH_KEY")
        self.azure_search_index = os.getenv("AZURE_SEARCH_INDEX_NAME", "banking-regulations")

        if not self.azure_search_endpoint or not self.azure_search_key:
            raise RuntimeError(
                "Azure AI Search credentials missing (AZURE_SEARCH_ENDPOINT / AZURE_SEARCH_KEY)."
            )

        self.azure_search_client = SearchClient(
            self.azure_search_endpoint,
            self.azure_search_index,
            AzureKeyCredential(self.azure_search_key),
        )
        self._load_product_map()

    def _load_product_map(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        m_path = os.path.join(base_dir, self.mapping_path) if not os.path.isabs(self.mapping_path) else self.mapping_path
        if os.path.exists(m_path):
            with open(m_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.product_map = {m["cfpb_product"]: m for m in data.get("mappings", [])}

    def get_governing_regulation(self, product: str) -> Optional[Dict[str, Any]]:
        if product in self.product_map:
            return self.product_map[product]
        for p_name, mapping in self.product_map.items():
            if p_name.lower() in product.lower() or product.lower() in p_name.lower():
                return mapping
        return None

    def _infer_canonical_rule_id(self, doc: Dict[str, Any], reg_id: str, query: str) -> str:
        """Map an Azure AI Search paragraph doc to its canonical CFR subsection ID."""
        content = (doc.get("content") or "").lower()
        q = query.lower()

        if reg_id == "REG_Z":
            if "acknowledge" in content or "30 days" in content or "(1) the creditor shall mail" in content:
                return "1026.13(c)(1)"
            if "two complete billing cycles" in content or "90 days" in content or "(2) the creditor shall comply" in content:
                return "1026.13(c)(2)"
            if "billing error notice" in content or "60 days" in content or "(b) billing error notice" in content:
                return "1026.13(b)"
            if "delinquent" in content or "adverse credit report" in content or "withhold" in content or "(d)" in content:
                return "1026.13(d)"
            if "(a) definition of billing error" in content:
                return "1026.13(a)"
            return "1026.13(c)(1)"

        if "20 business days" in content or ("new account" in q and "20" in content):
            return "1005.11(c)(3)"
        if "three business days" in content or "no error occurred" in content or "explanation" in content:
            return "1005.11(d)"
        if "reassert" in content or "no further responsibility" in content:
            return "1005.11(e)"
        if "45 days" in content or "forty-five day" in content or "provisionally credit" in content:
            return "1005.11(c)(2)"
        if "60 days" in content or "sixty days" in content or "oral or written" in content:
            return "1005.11(b)"
        return "1005.11(c)(1)"

    def search_regulation(
        self,
        query: str,
        product: Optional[str] = None,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """Retrieve matching regulatory paragraphs from live Azure AI Search."""
        governing_info = self.get_governing_regulation(product) if product else None
        target_reg = governing_info["governing_regulation"] if governing_info else None

        reg_filter = None
        if target_reg == "Regulation E":
            reg_filter = "regulation_id eq 'REG_E'"
        elif target_reg == "Regulation Z":
            reg_filter = "regulation_id eq 'REG_Z'"

        raw_docs = list(
            self.azure_search_client.search(
                search_text=query,
                filter=reg_filter,
                top=10,
            )
        )

        scored: List[tuple] = []
        q_lower = query.lower()

        for doc in raw_docs:
            reg_id = doc.get("regulation_id", "REG_E")
            canon_id = self._infer_canonical_rule_id(doc, reg_id, query)
            base_score = float(doc.get("@search.score") or 1.0)
            boost = 1.0

            if any(w in q_lower for w in ("how long", "deadline", "timeline", "days", "time")):
                if canon_id in ("1005.11(c)(1)", "1026.13(c)(1)", "1026.13(c)(2)"):
                    boost *= 2.0
            if ("provisional" in q_lower or "45" in q_lower or "90" in q_lower) and canon_id == "1005.11(c)(2)":
                boost *= 3.0
            if ("no error" in q_lower or "explanation" in q_lower) and canon_id == "1005.11(d)":
                boost *= 3.5
            if "new account" in q_lower and canon_id == "1005.11(c)(3)":
                boost *= 3.5
            if "acknowledg" in q_lower and canon_id == "1026.13(c)(1)":
                boost *= 4.0
            if ("notify" in q_lower or "notice" in q_lower) and "acknowledg" not in q_lower and canon_id in ("1005.11(b)", "1026.13(b)"):
                boost *= 3.5
            if "reassert" in q_lower and canon_id == "1005.11(e)":
                boost *= 3.5
            if any(w in q_lower for w in ("delinquent", "credit bureau", "pending")) and canon_id == "1026.13(d)":
                boost *= 3.5
            if ("types of transactions" in q_lower or "qualify as billing errors" in q_lower) and canon_id == "1026.13(a)":
                boost *= 3.5

            reg_label = "Regulation Z" if reg_id == "REG_Z" else "Regulation E"
            content = doc.get("content", "")
            scored.append(
                (
                    base_score * boost,
                    {
                        "id": canon_id,
                        "regulation": reg_label,
                        "citation": f"12 CFR § {canon_id}",
                        "title": doc.get("title", f"{reg_label} ({canon_id})"),
                        "summary": content,
                        "url": doc.get("source_url", ""),
                        "relevance_score": round(base_score * boost, 4),
                        "lookup_governing_reg": target_reg,
                        "search_provider": "Azure AI Search (complaint-regulations-rag-v2)",
                        "azure_doc_id": doc.get("id"),
                        "azure_paragraph_id": doc.get("paragraph_id"),
                    },
                )
            )

        scored.sort(key=lambda x: x[0], reverse=True)
        seen = set()
        results = []
        for _, item in scored:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            results.append(item)
            if len(results) >= top_k:
                break
        return results
