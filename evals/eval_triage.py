import csv
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from typing import Dict, Any
from src.agents.triage_agent import TriageAgent

def run_triage_evaluation(
    eval_csv_path: str = "data/complaints_held_back_eval.csv"
) -> Dict[str, Any]:
    """
    Evaluates Triage Agent against 30 held-back CFPB complaints.
    Measures Product Accuracy, Issue Accuracy, and Risk Distribution.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = os.path.join(base_dir, eval_csv_path) if not os.path.isabs(eval_csv_path) else eval_csv_path
    
    with open(full_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        eval_rows = list(reader)

    agent = TriageAgent()
    total = len(eval_rows)
    product_correct = 0
    issue_correct = 0
    risk_high_count = 0
    risk_normal_count = 0
    case_results = []

    for row in eval_rows:
        cid = row.get("Complaint ID", "")
        ground_product = row.get("Product", "").strip()
        ground_issue = row.get("Issue", "").strip()
        narrative = row.get("consumer_complaint_narrative") or row.get("narrative") or ""
        amt_str = row.get("disputed_amount")
        amount = float(amt_str) if amt_str else None
        tags = row.get("Tags")

        triage_out = agent.triage_complaint(
            narrative=narrative,
            disputed_amount=amount,
            tags=tags
        )

        pred_product = triage_out["product"]
        pred_issue = triage_out["issue"]
        risk_level = triage_out["risk_level"]

        p_match = (pred_product.lower() == ground_product.lower())
        if p_match:
            product_correct += 1

        # Issue match (strict or semantic equivalent)
        i_match = (pred_issue.lower() == ground_issue.lower()) or (ground_issue.lower() in pred_issue.lower()) or (pred_issue.lower() in ground_issue.lower())
        if i_match:
            issue_correct += 1

        if risk_level == "High":
            risk_high_count += 1
        else:
            risk_normal_count += 1

        case_results.append({
            "complaint_id": cid,
            "ground_product": ground_product,
            "pred_product": pred_product,
            "product_match": p_match,
            "ground_issue": ground_issue,
            "pred_issue": pred_issue,
            "issue_match": i_match,
            "risk_level": risk_level,
            "risk_reasons": triage_out["risk_reasons"]
        })

    product_acc = round((product_correct / total) * 100, 2) if total else 0.0
    issue_acc = round((issue_correct / total) * 100, 2) if total else 0.0

    return {
        "total_cases_evaluated": total,
        "product_accuracy_pct": product_acc,
        "product_correct": product_correct,
        "issue_accuracy_pct": issue_acc,
        "issue_correct": issue_correct,
        "risk_distribution": {
            "High": risk_high_count,
            "Normal": risk_normal_count
        },
        "case_details": case_results
    }

if __name__ == "__main__":
    res = run_triage_evaluation()
    print("=" * 60)
    print("PART F.1: TRIAGE EVALUATION ON 30 HELD-BACK COMPLAINTS")
    print("=" * 60)
    print(f"Total Evaluated:    {res['total_cases_evaluated']}")
    print(f"Product Accuracy:   {res['product_accuracy_pct']}% ({res['product_correct']}/{res['total_cases_evaluated']})")
    print(f"Issue Accuracy:     {res['issue_accuracy_pct']}% ({res['issue_correct']}/{res['total_cases_evaluated']})")
    print(f"Risk Distribution:  High: {res['risk_distribution']['High']} | Normal: {res['risk_distribution']['Normal']}")
    print("=" * 60)
