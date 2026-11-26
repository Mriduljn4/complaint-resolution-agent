import json
import os
import sys
import azure.functions as func

# Ensure root directory is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.deadline_tool import DeadlineTool

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
deadline_tool = DeadlineTool()

@app.route(route="calculate_deadline", methods=["POST"])
def calculate_deadline_function(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP endpoint for calculating statutory resolution deadlines under Reg E and Reg Z.
    Accepts error reported date, rule ID, and new account flag; returns target due date.
    """
    try:
        req_body = req.get_json()
        rule_id = req_body.get("rule_id", "1005.11(c)(1)")
        error_reported_date = req_body.get("error_reported_date")
        is_new_account = bool(req_body.get("is_new_account", False))
        transaction_type = req_body.get("transaction_type")

        if not error_reported_date:
            return func.HttpResponse(
                json.dumps({"error": "error_reported_date is required in YYYY-MM-DD format"}),
                status_code=400,
                mimetype="application/json"
            )

        result = deadline_tool.calculate_deadline(
            rule_id=rule_id,
            error_reported_date=error_reported_date,
            is_new_account=is_new_account,
            transaction_type=transaction_type
        )

        return func.HttpResponse(
            json.dumps(result, indent=2),
            status_code=200,
            mimetype="application/json"
        )
    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )

@app.route(route="health", methods=["GET"])
def health_check(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"status": "healthy", "service": "Azure Function - Deadline Tool", "stack": "Microsoft Azure"}),
        status_code=200,
        mimetype="application/json"
    )
