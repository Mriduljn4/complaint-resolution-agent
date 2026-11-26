import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class CaseDatabase:
    """SQLite persistence layer for complaint cases, HITL approvals, and audit logs."""

    def __init__(self, db_path: str = "data/complaints.db"):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.db_path = db_path if os.path.isabs(db_path) else os.path.join(base_dir, db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    narrative TEXT,
                    sanitized_narrative TEXT,
                    product TEXT,
                    issue TEXT,
                    risk_level TEXT,
                    disputed_amount REAL,
                    error_reported_date TEXT,
                    is_new_account INTEGER DEFAULT 0,
                    rule_id TEXT,
                    rule_citation TEXT,
                    due_date TEXT,
                    approval_status TEXT,
                    approver TEXT,
                    approval_comments TEXT,
                    draft_reply TEXT,
                    case_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    complaint_id TEXT NOT NULL,
                    rule_cited TEXT,
                    due_date TEXT,
                    model TEXT,
                    prompt_version TEXT,
                    decision TEXT,
                    approver TEXT,
                    timestamp TEXT NOT NULL,
                    risk_level TEXT,
                    product TEXT,
                    issue TEXT,
                    execution_trace_json TEXT
                )
                """
            )

    def save_case(self, case_record: Dict[str, Any]) -> None:
        """Insert or update a full complaint case record."""
        now_iso = datetime.now(timezone.utc).isoformat()
        inp = case_record.get("complaint_input", {})
        triage = case_record.get("triage", {})
        rule = case_record.get("rule", {})
        deadline = case_record.get("deadline", {})
        approval = case_record.get("approval", {})

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cases (
                    case_id, created_at, updated_at, narrative, sanitized_narrative,
                    product, issue, risk_level, disputed_amount, error_reported_date,
                    is_new_account, rule_id, rule_citation, due_date,
                    approval_status, approver, approval_comments, draft_reply, case_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    narrative = excluded.narrative,
                    sanitized_narrative = excluded.sanitized_narrative,
                    product = excluded.product,
                    issue = excluded.issue,
                    risk_level = excluded.risk_level,
                    disputed_amount = excluded.disputed_amount,
                    error_reported_date = excluded.error_reported_date,
                    is_new_account = excluded.is_new_account,
                    rule_id = excluded.rule_id,
                    rule_citation = excluded.rule_citation,
                    due_date = excluded.due_date,
                    approval_status = excluded.approval_status,
                    approver = excluded.approver,
                    approval_comments = excluded.approval_comments,
                    draft_reply = excluded.draft_reply,
                    case_json = excluded.case_json
                """,
                (
                    str(case_record["case_id"]),
                    now_iso,
                    now_iso,
                    inp.get("narrative", ""),
                    inp.get("sanitized_narrative", ""),
                    triage.get("product", ""),
                    triage.get("issue", ""),
                    triage.get("risk_level", "Normal"),
                    inp.get("disputed_amount"),
                    inp.get("error_reported_date", ""),
                    1 if inp.get("is_new_account") else 0,
                    rule.get("rule_id", ""),
                    rule.get("citation", ""),
                    deadline.get("due_date", ""),
                    approval.get("decision", "Pending"),
                    approval.get("approver", ""),
                    approval.get("comments", ""),
                    case_record.get("draft_reply", ""),
                    json.dumps(case_record),
                ),
            )

    def get_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT case_json FROM cases WHERE case_id = ?",
                (str(case_id),),
            ).fetchone()
        return json.loads(row["case_json"]) if row else None

    def list_cases(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT case_json FROM cases ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        return [json.loads(r["case_json"]) for r in rows]

    def update_case_approval(self, case_id: str, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        case_data = self.get_case(case_id)
        if not case_data:
            return None

        approval = case_data.setdefault("approval", {})
        approval["decision"] = record.get("approval_status", approval.get("decision"))
        approval["approver"] = record.get("approver", approval.get("approver"))
        approval["comments"] = record.get("comments", approval.get("comments"))
        approval["timestamp"] = record.get("decision_time", approval.get("timestamp"))

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE cases
                SET updated_at = ?, approval_status = ?, approver = ?, approval_comments = ?, case_json = ?
                WHERE case_id = ?
                """,
                (
                    now_iso,
                    approval["decision"],
                    approval["approver"],
                    approval["comments"],
                    json.dumps(case_data),
                    str(case_id),
                ),
            )
        return case_data

    def clear_cases(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM cases")

    def insert_audit_log(self, entry: Dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs (
                    complaint_id, rule_cited, due_date, model, prompt_version,
                    decision, approver, timestamp, risk_level, product, issue, execution_trace_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(entry.get("complaint_id", "")),
                    entry.get("rule_cited", ""),
                    entry.get("due_date", ""),
                    entry.get("model", ""),
                    entry.get("prompt_version", ""),
                    entry.get("decision", ""),
                    entry.get("approver", ""),
                    entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
                    entry.get("risk_level", "Normal"),
                    entry.get("product", ""),
                    entry.get("issue", ""),
                    json.dumps(entry.get("execution_trace", [])),
                ),
            )

    def list_audit_logs(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_logs ORDER BY id ASC"
            ).fetchall()
        logs: List[Dict[str, Any]] = []
        for r in rows:
            logs.append(
                {
                    "complaint_id": r["complaint_id"],
                    "rule_cited": r["rule_cited"],
                    "due_date": r["due_date"],
                    "model": r["model"],
                    "prompt_version": r["prompt_version"],
                    "decision": r["decision"],
                    "approver": r["approver"],
                    "timestamp": r["timestamp"],
                    "risk_level": r["risk_level"],
                    "product": r["product"],
                    "issue": r["issue"],
                    "execution_trace": json.loads(r["execution_trace_json"] or "[]"),
                }
            )
        return logs
