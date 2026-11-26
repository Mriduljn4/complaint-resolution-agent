import json
import azure.functions as func
from azure_functions.function_app import calculate_deadline_function, health_check
from src.mcp_server import handle_mcp_call, MCP_TOOLS

def test_azure_function_calculate_deadline():
    payload = json.dumps({
        "rule_id": "1005.11(c)(1)",
        "error_reported_date": "2026-03-02",
        "is_new_account": False
    }).encode("utf-8")

    req = func.HttpRequest(
        method="POST",
        url="/api/calculate_deadline",
        body=payload,
        headers={"Content-Type": "application/json"}
    )

    resp = calculate_deadline_function(req)
    assert resp.status_code == 200
    body = json.loads(resp.get_body().decode("utf-8"))
    assert body["due_date"] == "2026-03-16"
    assert body["regulation"] == "Regulation E"

def test_azure_function_health():
    req = func.HttpRequest(method="GET", url="/api/health", body=b"")
    resp = health_check(req)
    assert resp.status_code == 200
    assert "healthy" in resp.get_body().decode("utf-8")

def test_mcp_tools_and_execution():
    assert len(MCP_TOOLS) == 3
    tool_names = [t["name"] for t in MCP_TOOLS]
    assert "calculate_compliance_deadline" in tool_names
    assert "search_banking_regulations" in tool_names

    res = handle_mcp_call(
        "calculate_compliance_deadline",
        {"rule_id": "1005.11(c)(1)", "error_reported_date": "2026-03-02", "is_new_account": False}
    )
    assert res["due_date"] == "2026-03-16"
