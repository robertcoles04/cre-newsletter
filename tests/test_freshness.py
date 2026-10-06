"""Data-freshness guard: as_of_dates, stale_data notes, and the publish-gate hard stop."""
import json
from datetime import date, timedelta

from src import publish
from src.checks import check_issue
from src.factsheet import build_factsheet
from src.freshness import business_days_old, core_stale, stale_series
from src.models import RatePoint, ReitQuote
from src.store import connect, save_rates

FRI = date(2026, 10, 9)   # previous business day = Thu Oct 8
MON = date(2026, 10, 12)  # previous business day = Fri Oct 9


def stale(asof, run=FRI):
    return dict(stale_series(asof, run))


# ---------------------------------------------------------------- business days

def test_monday_run_with_friday_data_is_fresh():
    assert business_days_old(date(2026, 10, 9), MON) == 0
    assert stale({"DGS10": "2026-10-09"}, MON) == {}


def test_weekend_is_skipped_when_counting():
    assert business_days_old(date(2026, 10, 9), date(2026, 10, 13)) == 1  # Tue run, Fri data
    assert business_days_old(date(2026, 10, 8), MON) == 1                 # Thu data, Mon run


# ---------------------------------------------------------------- cadence thresholds

def test_daily_threshold_is_more_than_3_business_days():
    assert stale({"DGS5": "2026-10-05"}) == {}                      # 3 old (Tue, Wed, Thu)
    assert stale({"DGS5": "2026-10-02"}) == {"5-Year": "2026-10-02"}  # 4 old
    for key in ("DGS10", "DGS2", "DGS30", "SOFR", "DFF", "HY_OAS", "VNQ", "REIT"):
        assert len(stale({key: "2026-10-02"})) == 1, key


def test_weekly_threshold_is_more_than_10_business_days():
    assert stale({"MORTGAGE30US": "2026-09-24"}) == {}
    assert stale({"MORTGAGE30US": "2026-09-23"}) == {"30-Year Mortgage": "2026-09-23"}
    assert stale({"BANK_CRE_LOANS": "2026-09-23"}) == {"Bank CRE loans": "2026-09-23"}


def test_monthly_threshold_is_more_than_45_days():
    assert stale({"CMBS_DQ": (FRI - timedelta(days=45)).isoformat()}) == {}
    old = (FRI - timedelta(days=46)).isoformat()
    assert stale({"CMBS_DQ": old}) == {"CMBS delinquency": old}


def test_quarterly_threshold_is_more_than_200_days():
    assert stale({"BANK_CRE_DQ": (FRI - timedelta(days=200)).isoformat()}) == {}
    old = (FRI - timedelta(days=201)).isoformat()
    assert stale({"BANK_CRE_DQ": old}) == {"Bank CRE delinquency": old}


def test_missing_optional_series_is_not_stale():
    assert stale({}) == {}


# ---------------------------------------------------------------- editor notes

def _sheet(asof, day="2026-10-09"):
    return {"date": day, "day_type": "weekday", "values": {}, "as_of_dates": asof}


def _stale_notes(sheet):
    return [p["detail"] for p in check_issue("# T\n", sheet, []) if p["kind"] == "stale_data"]


def test_check_issue_reports_stale_data():
    notes = _stale_notes(_sheet({"DGS10": "2026-10-08", "SOFR": "2026-10-08",
                                 "MORTGAGE30US": "2026-09-01"}))
    assert notes == ["30-Year Mortgage last updated 2026-09-01"]


def test_check_issue_flags_missing_core_rate_and_skips_old_sheets():
    assert _stale_notes(_sheet({"DGS10": "2026-10-08"})) == ["SOFR has no data"]
    sheet = _sheet({})
    del sheet["as_of_dates"]
    assert _stale_notes(sheet) == []


# ---------------------------------------------------------------- fact sheet

def test_factsheet_records_as_of_dates():
    conn = connect(":memory:")
    save_rates(conn, [RatePoint("DGS10", date(2026, 10, 8), 4.6),
                      RatePoint("DGS30", date(2026, 10, 7), 5.0),
                      RatePoint("SOFR", date(2026, 10, 6), 4.3),
                      RatePoint("DFEDTARU", date(2026, 10, 8), 4.0),
                      RatePoint("MORTGAGE30US", date(2026, 10, 1), 6.5)])
    quotes = [ReitQuote("VNQ", date(2026, 10, 8), 85.0, 0.5),
              ReitQuote("O", date(2026, 10, 7), 60.0, 1.0)]
    sheet = build_factsheet(conn, FRI, None, quotes, [], None,
                            cmbs={"CMBS_DQ_CHG": "+1 bps", "CMBS_DQ_MONTH": "Sept 2026",
                                  "CMBS_DQ_DATE": "2026-10-03"})
    assert sheet["as_of_dates"] == {
        "DGS10": "2026-10-08", "DGS30": "2026-10-07", "SOFR": "2026-10-06",
        "DFF": "2026-10-08", "MORTGAGE30US": "2026-10-01", "VNQ": "2026-10-08",
        "REIT": "2026-10-07", "CMBS_DQ": "2026-10-03"}


# ---------------------------------------------------------------- publish gate

def _issue(root, day, data, md="# T\n\n## Top Stories\nText.\n"):
    d = root / "issues"
    d.mkdir(exist_ok=True)
    (d / f"{day}.md").write_text(md, encoding="utf-8")
    (d / f"{day}.json").write_text(json.dumps(data), encoding="utf-8")


def test_gate_refuses_stale_10_year(tmp_path):
    # Fri Oct 9 run: prev business day Thu Oct 8; Sep 30 (Wed) is 6 business days old.
    _issue(tmp_path, "2026-10-09", {"as_of_dates": {"DGS10": "2026-09-30", "SOFR": "2026-10-08"}})
    assert publish.gate(tmp_path, "2026-10-09") == [
        "core rates are stale: 10-Year last updated 2026-09-30"]
    assert publish.main(["2026-10-09", "--root", str(tmp_path)]) == 1


def test_gate_allows_5_business_days_and_refuses_6(tmp_path):
    _issue(tmp_path, "2026-10-09", {"as_of_dates": {"DGS10": "2026-10-01", "SOFR": "2026-10-01"}})
    assert publish.gate(tmp_path, "2026-10-09") == []   # Oct 1 is 5 old
    _issue(tmp_path, "2026-10-09", {"as_of_dates": {"DGS10": "2026-10-08", "SOFR": "2026-09-30"}})
    assert publish.gate(tmp_path, "2026-10-09") == [
        "core rates are stale: SOFR last updated 2026-09-30"]


def test_gate_refuses_missing_core_rate(tmp_path):
    _issue(tmp_path, "2026-10-09", {"as_of_dates": {"DGS10": "2026-10-08"}})
    assert publish.gate(tmp_path, "2026-10-09") == ["core rates are stale: SOFR has no data"]


def test_gate_passes_fresh_core_rates(tmp_path):
    _issue(tmp_path, "2026-10-09", {"as_of_dates": {"DGS10": "2026-10-08", "SOFR": "2026-10-08"}})
    assert publish.gate(tmp_path, "2026-10-09") == []


def test_old_json_without_as_of_dates_still_passes(tmp_path):
    _issue(tmp_path, "2026-10-05", {"date": "2026-10-05", "values": {}})
    assert publish.gate(tmp_path, "2026-10-05") == []
    assert publish.main(["2026-10-05", "--root", str(tmp_path)]) == 0


def test_core_stale_helper():
    assert core_stale({"DGS10": "2026-10-08", "SOFR": "2026-10-08"}, FRI) == []
