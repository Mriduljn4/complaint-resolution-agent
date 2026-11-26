from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
import holidays


class DeadlineTool:
    """
    Simple Python date calculator (no LLM math) for Reg E and Reg Z due dates.
    Automatically skips Saturdays, Sundays, and official US Federal Holidays.
    """

    def __init__(self):
        self.us_holidays = holidays.country_holidays("US", observed=True)

    def is_weekend(self, d: date) -> bool:
        return d.weekday() >= 5

    def is_federal_holiday(self, d: date) -> Optional[str]:
        return self.us_holidays.get(d)

    def is_business_day(self, d: date) -> bool:
        return not self.is_weekend(d) and not self.is_federal_holiday(d)

    def _resolve_rule(
        self, rule_id: str, is_new_account: bool, transaction_type: Optional[str]
    ) -> Tuple[str, str, str, int]:
        """Return (regulation, rule_citation, day_type, days_to_count)."""
        rule = rule_id.strip()
        if "1026" in rule or "z" in rule.lower() or "credit" in rule.lower():
            if "1026.13(c)(2)" in rule:
                return "Regulation Z", "12 CFR § 1026.13(c)(2)", "calendar_days", 90
            return "Regulation Z", "12 CFR § 1026.13(c)(1)", "calendar_days", 30

        if is_new_account or "1005.11(c)(3)" in rule:
            return "Regulation E", "12 CFR § 1005.11(c)(3)", "business_days", 20
        if "1005.11(c)(2)" in rule:
            days = 90 if (is_new_account or transaction_type in ("POS_DEBIT", "FOREIGN_TRANSACTION")) else 45
            return "Regulation E", "12 CFR § 1005.11(c)(2)", "calendar_days", days
        if "1005.11(d)" in rule:
            return "Regulation E", "12 CFR § 1005.11(d)", "business_days", 3
        return "Regulation E", "12 CFR § 1005.11(c)(1)", "business_days", 10

    def calculate_deadline(
        self,
        rule_id: str,
        error_reported_date: Union[str, date],
        is_new_account: bool = False,
        transaction_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute statutory due date and return a step-by-step calculation trace."""
        start_dt = (
            datetime.strptime(error_reported_date.split("T")[0].strip(), "%Y-%m-%d").date()
            if isinstance(error_reported_date, str)
            else error_reported_date
        )
        reg_name, rule_citation, day_type, days_to_count = self._resolve_rule(
            rule_id, is_new_account, transaction_type
        )

        working_log: List[Dict[str, Any]] = []
        holidays_skipped: List[Dict[str, str]] = []
        weekend_days_skipped = 0

        if day_type == "business_days":
            current_dt = start_dt
            counted = 0
            while counted < days_to_count:
                current_dt += timedelta(days=1)
                dt_str = current_dt.strftime("%Y-%m-%d")
                day_name = current_dt.strftime("%A")

                if self.is_weekend(current_dt):
                    weekend_days_skipped += 1
                    status = "SKIPPED_WEEKEND"
                elif h_name := self.is_federal_holiday(current_dt):
                    holidays_skipped.append({"date": dt_str, "name": h_name})
                    status = f"SKIPPED_FEDERAL_HOLIDAY ({h_name})"
                else:
                    counted += 1
                    status = "COUNTED_BUSINESS_DAY"

                working_log.append({
                    "date": dt_str,
                    "day_of_week": day_name,
                    "status": status,
                    "running_count": counted,
                })
            due_date = current_dt
        else:
            due_date = start_dt + timedelta(days=days_to_count)
            while not self.is_business_day(due_date):
                if self.is_weekend(due_date):
                    weekend_days_skipped += 1
                if h_name := self.is_federal_holiday(due_date):
                    holidays_skipped.append({"date": due_date.strftime("%Y-%m-%d"), "name": h_name})
                due_date += timedelta(days=1)

        elapsed = (due_date - start_dt).days
        holiday_note = (
            f"{len(holidays_skipped)} federal holiday(s) ({', '.join(h['name'] for h in holidays_skipped)})"
            if holidays_skipped
            else "0 holidays"
        )
        if day_type == "business_days":
            explanation = (
                f"Starting on {start_dt.strftime('%A, %Y-%m-%d')}, counted {days_to_count} business days under "
                f"{rule_citation} ({'New Account extension' if is_new_account else 'Standard timeline'}). "
                f"Skipped {weekend_days_skipped} weekend day(s) and {holiday_note}. "
                f"Statutory deadline is {due_date.strftime('%A, %Y-%m-%d')} ({elapsed} calendar days total)."
            )
        else:
            explanation = (
                f"Calculated {days_to_count} calendar days under {rule_citation} starting {start_dt.strftime('%Y-%m-%d')}. "
                f"{holiday_note} skipped if applicable. Statutory deadline is {due_date.strftime('%A, %Y-%m-%d')}."
            )

        return {
            "rule_id": rule_id.strip(),
            "rule_citation": rule_citation,
            "regulation": reg_name,
            "error_reported_date": start_dt.strftime("%Y-%m-%d"),
            "due_date": due_date.strftime("%Y-%m-%d"),
            "due_date_formatted": due_date.strftime("%B %d, %Y"),
            "days_allocated": days_to_count,
            "day_type": day_type,
            "is_new_account": is_new_account,
            "calculation_working": {
                "start_date": start_dt.strftime("%Y-%m-%d"),
                "due_date": due_date.strftime("%Y-%m-%d"),
                "days_counted": days_to_count,
                "calendar_days_elapsed": elapsed,
                "weekend_days_skipped": weekend_days_skipped,
                "holidays_skipped": holidays_skipped,
                "step_by_step_log": working_log[-12:],
                "explanation": explanation,
            },
        }
