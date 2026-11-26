#!/usr/bin/env python3
"""
Model Context Protocol (MCP) server providing banking compliance tools over JSON-RPC 2.0 (HTTP & stdio).
Exposes tools for deadline calculations, regulatory paragraph retrieval, and risk assessment.
"""
import json
import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.azure_config import load_azure_secrets_from_keyvault
load_azure_secrets_from_keyvault()

from src.agents.triage_agent import TriageAgent
from src.deadline_tool import DeadlineTool
from src.knowledge_base import RegulatoryKnowledgeBase

deadline_tool = DeadlineTool()
kb = RegulatoryKnowledgeBase()
triage_agent = TriageAgent()

MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_SERVER_INFO = {"name": "complaint-resolution-mcp", "version": "1.2.0"}

MCP_TOOLS = [
    {
        "name": "calculate_compliance_deadline",
        "description": "Calculates the exact statutory response deadline under Regulation E (§ 1005.11) or Regulation Z (§ 1026.13), skipping weekends and US federal holidays.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "description": "e.g. 1005.11(c)(1), 1005.11(c)(3), 1026.13(c)(1)"},
                "error_reported_date": {"type": "string", "description": "Date reported in YYYY-MM-DD"},
                "is_new_account": {"type": "boolean", "description": "Account established <= 30 days"},
            },
            "required": ["rule_id", "error_reported_date"],
        },
    },
    {
        "name": "search_banking_regulations",
        "description": "Searches US consumer protection regulations (Regulation E & Z) by paragraph ID, title, and citation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language query regarding banking error rules"},
                "product": {"type": "string", "description": "CFPB product name for domain lookup filtering"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "evaluate_complaint_risk",
        "description": "Evaluates whether a consumer complaint is High Risk or Normal Risk based on institutional bank policy.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "narrative": {"type": "string", "description": "Complaint narrative text"},
                "disputed_amount": {"type": "number", "description": "Amount in USD"},
            },
            "required": ["narrative"],
        },
    },
]


def handle_mcp_call(tool_name: str, arguments: Dict[str, Any]) -> Any:
    if tool_name == "calculate_compliance_deadline":
        return deadline_tool.calculate_deadline(
            rule_id=arguments.get("rule_id", "1005.11(c)(1)"),
            error_reported_date=arguments.get("error_reported_date"),
            is_new_account=arguments.get("is_new_account", False),
        )
    if tool_name == "search_banking_regulations":
        return kb.search_regulation(
            query=arguments.get("query", ""),
            product=arguments.get("product"),
            top_k=arguments.get("top_k", 2),
        )
    if tool_name == "evaluate_complaint_risk":
        return triage_agent.triage_complaint(
            narrative=arguments.get("narrative", ""),
            disputed_amount=arguments.get("disputed_amount"),
        )
    raise ValueError(f"Unknown MCP tool: {tool_name}")


def handle_mcp_jsonrpc(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Process an MCP JSON-RPC 2.0 request (used by both HTTP /mcp and stdio)."""
    req_id = payload.get("id")
    method = payload.get("method", "")
    params = payload.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": MCP_SERVER_INFO,
                "capabilities": {"tools": {}},
            },
        }
    if method == "notifications/initialized":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            output = handle_mcp_call(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(output, indent=2)}],
                    "structuredContent": output,
                    "isError": False,
                },
            }
        except Exception as err:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": str(err)}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--list-tools":
        print(json.dumps(MCP_TOOLS, indent=2))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "--stdio":
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = handle_mcp_jsonrpc(req)
                if req.get("id") is not None:
                    print(json.dumps(resp), flush=True)
            except Exception as err:
                print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(err)}}), flush=True)
        return

    sample_res = handle_mcp_call(
        "calculate_compliance_deadline",
        {"rule_id": "1005.11(c)(1)", "error_reported_date": "2026-03-02", "is_new_account": False},
    )
    print("MCP Server ready. Tools:")
    for t in MCP_TOOLS:
        print(f" - {t['name']}")
    print(f"Self-test due date: {sample_res['due_date']}")


if __name__ == "__main__":
    main()
