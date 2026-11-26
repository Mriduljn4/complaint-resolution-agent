import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from src.database.case_db import CaseDatabase


class AuditLogger:
    """
    Saves one permanent audit row per complaint in both SQLite (complaints.db)
    and audit_log.jsonl for full compliance tracking.
    """
    def __init__(self, log_path: str = "data/audit_log.jsonl", db: Optional[CaseDatabase] = None):
        self.log_path = log_path
        self.db = db or CaseDatabase()
        self._ensure_dir()

    def _ensure_dir(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        full_path = os.path.join(base_dir, self.log_path) if not os.path.isabs(self.log_path) else self.log_path
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        self.full_log_path = full_path

    def log_case(
        self,
        complaint_id: str,
        rule_cited: str,
        due_date: str,
        decision: str,
        approver: str,
        model: str = "gpt-4.1",
        prompt_version: str = "v1.2.0",
        risk_level: str = "Normal",
        product: str = "",
        issue: str = "",
        execution_trace: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Writes one audit row per complaint case to SQLite and JSONL."""
        now_iso = datetime.now(timezone.utc).isoformat()
        audit_entry = {
            "complaint_id": str(complaint_id),
            "rule_cited": rule_cited,
            "due_date": due_date,
            "model": model,
            "prompt_version": prompt_version,
            "decision": decision,
            "approver": approver,
            "timestamp": now_iso,
            "risk_level": risk_level,
            "product": product,
            "issue": issue,
            "execution_trace": execution_trace or []
        }

        self.db.insert_audit_log(audit_entry)
        with open(self.full_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(audit_entry) + "\n")

        return audit_entry

    def get_all_logs(self) -> List[Dict[str, Any]]:
        db_logs = self.db.list_audit_logs()
        if db_logs:
            return db_logs
        if not os.path.exists(self.full_log_path):
            return []
        entries = []
        with open(self.full_log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries
