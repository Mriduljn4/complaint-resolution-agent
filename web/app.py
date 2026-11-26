import os
import sys
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents.orchestrator import ComplaintCopilotOrchestrator
from src.database.case_db import CaseDatabase
from src.mcp_server import MCP_SERVER_INFO, MCP_TOOLS, handle_mcp_jsonrpc
from src.telemetry.audit_logger import AuditLogger

app = FastAPI(title="Complaint Resolution Copilot API", version="1.2.0")

# Mount static HTML portals
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Core services shared across API routes
case_db = CaseDatabase()
audit_logger = AuditLogger(db=case_db)
orchestrator = ComplaintCopilotOrchestrator(audit_logger=audit_logger)


class ComplaintRequest(BaseModel):
    complaint_id: Optional[str] = None
    narrative: str
    error_reported_date: Optional[str] = "2026-11-20"
    is_new_account: Optional[bool] = False
    disputed_amount: Optional[float] = None
    transaction_type: Optional[str] = None
    tags: Optional[str] = None
    company: Optional[str] = "National Retail Bank"
    auto_approve_high_risk: Optional[bool] = False


class ApprovalDecisionRequest(BaseModel):
    complaint_id: str
    decision: Optional[str] = None
    action: Optional[str] = None
    approver: Optional[str] = None
    reviewer: Optional[str] = None
    comments: Optional[str] = None


def _read_html(filename: str) -> str:
    """Read an HTML page from web/static."""
    path = os.path.join(static_dir, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return f"<h1>{filename} not found</h1>"


def _save_approval(complaint_id: str, decision: str, approver: str, comments: str):
    """Shared helper to record Approve/Reject decisions in DB and audit log."""
    record = orchestrator.approval_service.submit_manual_decision(
        complaint_id=complaint_id,
        decision=decision,
        approver=approver,
        comments=comments,
    )
    updated_case = case_db.update_case_approval(complaint_id, record) or {}
    audit_logger.log_case(
        complaint_id=complaint_id,
        rule_cited=updated_case.get("rule", {}).get("citation", "None"),
        due_date=updated_case.get("deadline", {}).get("due_date", "N/A"),
        decision=record["approval_status"],
        approver=record["approver"],
        model=orchestrator.model_name,
        prompt_version=orchestrator.prompt_version,
        risk_level=updated_case.get("triage", {}).get("risk_level", "High"),
        product=updated_case.get("triage", {}).get("product", ""),
        issue=updated_case.get("triage", {}).get("issue", ""),
        execution_trace=updated_case.get("execution_trace", []),
    )
    return record


# --- Web UI Pages ---
@app.get("/", response_class=HTMLResponse)
@app.get("/customer", response_class=HTMLResponse)
async def customer_portal():
    return _read_html("simple_complaint.html")


@app.get("/approval", response_class=HTMLResponse)
@app.get("/hitl", response_class=HTMLResponse)
@app.get("/compliance", response_class=HTMLResponse)
async def approval_portal():
    return _read_html("approval.html")


@app.get("/presentation", response_class=HTMLResponse)
@app.get("/slides", response_class=HTMLResponse)
async def presentation_page():
    return _read_html("presentation.html")


# --- Complaint Processing & Case APIs ---
@app.post("/api/process")
async def process_complaint(req: ComplaintRequest):
    """Run one complaint through Guardrail -> Triage -> Azure AI Search -> Deadline -> Reply -> Approval."""
    try:
        complaint_dict = {
            "Complaint ID": req.complaint_id or f"WEB-{os.urandom(3).hex().upper()}",
            "consumer_complaint_narrative": req.narrative,
            "error_reported_date": req.error_reported_date or "2026-11-20",
            "is_new_account": bool(req.is_new_account),
            "disputed_amount": req.disputed_amount,
            "transaction_type": req.transaction_type,
            "Tags": req.tags,
            "Company": req.company or "National Retail Bank",
        }
        res = orchestrator.process_complaint(
            complaint_dict=complaint_dict,
            auto_approve_high_risk=bool(req.auto_approve_high_risk),
        )
        case_db.save_case(res)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cases")
async def list_cases():
    cases = case_db.list_cases()
    return {"total": len(cases), "count": len(cases), "cases": cases}


@app.post("/api/cases/clear")
async def clear_cases():
    case_db.clear_cases()
    return {"status": "SUCCESS", "message": "All cases cleared"}


@app.get("/api/cases/{case_id}")
async def get_case(case_id: str):
    case_data = case_db.get_case(case_id)
    if not case_data:
        raise HTTPException(status_code=404, detail="Case not found")
    return case_data


# --- Human-in-the-Loop (HITL) & Teams Bot Approval APIs ---
@app.post("/api/approval")
async def review_approval(req: ApprovalDecisionRequest):
    decision = req.decision or req.action or "Approved"
    approver = req.approver or req.reviewer or "Compliance_Lead_Reviewer"
    comments = req.comments or f"{decision} by {approver}."
    return _save_approval(req.complaint_id, decision, approver, comments)


@app.post("/api/bot/approval")
async def bot_approval(req: ApprovalDecisionRequest):
    """Handle Approve/Reject clicks coming from Microsoft Teams / Power Automate."""
    decision = req.decision or req.action or "Approved"
    approver = req.approver or req.reviewer or "Compliance_Officer"
    comments = req.comments or f"{decision} via Microsoft Teams."
    record = _save_approval(req.complaint_id, decision, approver, comments)
    return {"status": "SUCCESS", "channel": "Microsoft Teams", "record": record}


@app.post("/api/bot/messages")
async def bot_messages(request: Request):
    """Webhook for Azure Bot Service (Teams Adaptive Card actions)."""
    activity = await request.json() if await request.body() else {}
    text = (activity.get("text") or "").strip()
    value = activity.get("value") or {}
    action = value.get("action") or ("approve" if "approve" in text.lower() else ("reject" if "reject" in text.lower() else None))
    complaint_id = value.get("complaint_id") or next((p for p in text.split() if p.startswith(("CASE-", "WEB-"))), "")

    if action and complaint_id:
        decision = "Approved" if action.lower() == "approve" else "Rejected"
        approver = activity.get("from", {}).get("name") or "Azure_Bot_Officer"
        _save_approval(complaint_id, decision, approver, "Action submitted via Microsoft Teams.")
        return {"type": "message", "text": f"Case {complaint_id} marked as {decision} by {approver}."}

    return {"type": "message", "text": "Complaint Resolution Bot active."}


# --- MCP Server & Container Health Probes ---
@app.get("/mcp")
async def mcp_info():
    return {"server": MCP_SERVER_INFO, "transport": "streamable-http", "tools": MCP_TOOLS}


@app.post("/mcp")
async def mcp_jsonrpc(request: Request):
    return handle_mcp_jsonrpc(await request.json())


@app.get("/liveness")
async def liveness():
    return {"status": "ok"}


@app.get("/readiness")
async def readiness():
    return {"status": "ready"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=False)
