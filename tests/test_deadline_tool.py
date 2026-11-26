import pytest
from src.deadline_tool import DeadlineTool

@pytest.fixture
def deadline_tool():
    return DeadlineTool()

def test_1_standard_business_days_with_weekends(deadline_tool):
    """
    Test 1: Standard 10 business days under Reg E § 1005.11(c)(1).
    Start: Monday 2026-03-02 (no holidays).
    10 business days skips 2 weekends (Sat/Sun Mar 7-8 and Mar 14-15).
    Due date should be Monday 2026-03-16.
    """
    res = deadline_tool.calculate_deadline(
        rule_id="1005.11(c)(1)",
        error_reported_date="2026-03-02",
        is_new_account=False
    )
    assert res["due_date"] == "2026-03-16"
    assert res["calculation_working"]["days_counted"] == 10
    assert res["calculation_working"]["weekend_days_skipped"] == 4
    assert len(res["calculation_working"]["holidays_skipped"]) == 0

def test_2_holiday_skipping_thanksgiving(deadline_tool):
    """
    Test 2: Holiday skipping test.
    Start: Friday 2026-11-20.
    10 business days spans:
    - Weekend Nov 21-22
    - Thanksgiving Day holiday on Thursday 2026-11-26
    - Weekend Nov 28-29
    - Weekend Dec 5-6
    Due date should be Monday 2026-12-07.
    """
    res = deadline_tool.calculate_deadline(
        rule_id="1005.11(c)(1)",
        error_reported_date="2026-11-20",
        is_new_account=False
    )
    assert res["due_date"] == "2026-12-07"
    assert res["calculation_working"]["days_counted"] == 10
    assert res["calculation_working"]["weekend_days_skipped"] == 6
    holidays = res["calculation_working"]["holidays_skipped"]
    assert any(h["name"] == "Thanksgiving Day" and h["date"] == "2026-11-26" for h in holidays)

def test_3_new_account_extension_20_days(deadline_tool):
    """
    Test 3: New account extension under Reg E § 1005.11(c)(3).
    Start: Monday 2026-03-02, is_new_account=True.
    Should count 20 business days (skipping 4 weekends).
    Due date should be Monday 2026-03-30.
    """
    res = deadline_tool.calculate_deadline(
        rule_id="1005.11(c)(1)",
        error_reported_date="2026-03-02",
        is_new_account=True
    )
    assert res["rule_citation"] == "12 CFR § 1005.11(c)(3)"
    assert res["days_allocated"] == 20
    assert res["due_date"] == "2026-03-30"
    assert res["calculation_working"]["days_counted"] == 20
    assert res["calculation_working"]["weekend_days_skipped"] == 8

def test_4_reg_z_credit_card_acknowledgment(deadline_tool):
    """
    Test 4: Regulation Z § 1026.13(c)(1) billing error acknowledgment (30 calendar days).
    Start: 2026-05-01. 30 calendar days is Sunday 2026-05-31.
    Per banking rules, rolls to Monday 2026-06-01 (next business day).
    """
    res = deadline_tool.calculate_deadline(
        rule_id="1026.13(c)(1)",
        error_reported_date="2026-05-01",
        is_new_account=False
    )
    assert res["regulation"] == "Regulation Z"
    assert res["days_allocated"] == 30
    assert res["due_date"] == "2026-06-01"

def test_5_year_end_spanning_christmas_and_new_years(deadline_tool):
    """
    Test 5: Year-end boundary test.
    Start: Monday 2026-12-21.
    10 business days must skip:
    - Christmas Day (Friday 2026-12-25)
    - Weekend Dec 26-27
    - New Year's Day (Friday 2027-01-01)
    - Weekend Jan 2-3
    - Weekend Jan 9-10
    Due date should be Wednesday 2027-01-06.
    """
    res = deadline_tool.calculate_deadline(
        rule_id="1005.11(c)(1)",
        error_reported_date="2026-12-21",
        is_new_account=False
    )
    assert res["due_date"] == "2027-01-06"
    assert res["calculation_working"]["days_counted"] == 10
    holidays = res["calculation_working"]["holidays_skipped"]
    assert any(h["name"] == "Christmas Day" for h in holidays)
    assert any(h["name"] == "New Year's Day" for h in holidays)
