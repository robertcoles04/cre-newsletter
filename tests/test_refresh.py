import json
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from src import refresh
from src.models import RatePoint

DAY = "2026-10-15"
REFRESH_WF = Path(".github/workflows/refresh.yml")


def _issue(**values) -> dict:
    base = {
        "MORTGAGE30US": "7.40%", "MORTGAGE30US_CHG": "+12 bps", "MORTGAGE30US_ASOF": "Oct 8",
        "HY_OAS": "3.16%", "HY_OAS_CHG": "+5 bps", "HY_OAS_DATE": "2026-10-13",
        "HY_OAS_ASOF": "Oct 13", "RATES_ASOF": "Oct 14", "DGS10": "4.20%",
    }
    base.update(values)
    return {
        "date": DAY,
        "as_of_dates": {"DGS10": "2026-10-14", "HY_OAS": "2026-10-13",
                        "MORTGAGE30US": "2026-10-08"},
        "charts": {"mortgage": {"alt": "old alt", "width": 360, "height": 260}},
        "values": base,
    }


def _weekly(last: date, last_value: float, n: int = 26) -> list[RatePoint]:
    pts = [RatePoint("MORTGAGE30US", last - timedelta(weeks=i), 7.0 + i * 0.01)
           for i in range(n - 1, 0, -1)]
    return pts + [RatePoint("MORTGAGE30US", last, last_value)]


def _hy(*pairs) -> list[RatePoint]:
    return [RatePoint("BAMLH0A0HYM2", date.fromisoformat(d), v) for d, v in pairs]


@pytest.fixture
def root(tmp_path):
    (tmp_path / "issues" / "img").mkdir(parents=True)
    (tmp_path / "issues" / "published.json").write_text(
        json.dumps(["2026-10-14", DAY]), encoding="utf-8")
    (tmp_path / "issues" / f"{DAY}.json").write_text(json.dumps(_issue()), encoding="utf-8")
    (tmp_path / "issues" / f"{DAY}.md").write_text("# body {{MORTGAGE30US}}", encoding="utf-8")
    return tmp_path


def _fake_fred(monkeypatch, mort, hy):
    def fetch(sid, key, start, client):
        return {"MORTGAGE30US": mort, "BAMLH0A0HYM2": hy}[sid]
    monkeypatch.setattr(refresh.fred, "fetch_series", fetch)


def test_latest_issue_is_newest_published(root):
    assert refresh.latest_issue(root) == DAY


def test_mortgage_update_sets_value_change_asof_and_alt():
    data = _issue()
    pts = _weekly(date(2026, 10, 15), 7.40)
    pts[-2] = RatePoint("MORTGAGE30US", pts[-2].date, 7.40)
    assert refresh.apply_mortgage(data, pts)
    v = data["values"]
    assert (v["MORTGAGE30US"], v["MORTGAGE30US_CHG"], v["MORTGAGE30US_ASOF"]) == \
        ("7.40%", "0 bps", "Oct 15")
    assert data["as_of_dates"]["MORTGAGE30US"] == "2026-10-15"
    assert data["charts"]["mortgage"]["alt"].endswith("to 7.40% on Oct 15.")


def test_mortgage_asof_dropped_when_same_day_as_10y():
    data = _issue()
    assert refresh.apply_mortgage(data, _weekly(date(2026, 10, 14), 7.10))
    assert "MORTGAGE30US_ASOF" not in data["values"]


def test_hy_oas_asof_dropped_when_it_equals_rates_asof():
    data = _issue()
    assert refresh.apply_hy_oas(data, _hy(("2026-10-13", 3.16), ("2026-10-14", 3.25)))
    v = data["values"]
    assert (v["HY_OAS"], v["HY_OAS_CHG"], v["HY_OAS_DATE"]) == ("3.25%", "+9 bps", "2026-10-14")
    assert "HY_OAS_ASOF" not in v
    assert data["as_of_dates"]["HY_OAS"] == "2026-10-14"


def test_hy_oas_asof_kept_when_older_than_rates():
    data = _issue(RATES_ASOF="Oct 15")
    data["as_of_dates"]["HY_OAS"] = "2026-10-12"
    assert refresh.apply_hy_oas(data, _hy(("2026-10-12", 3.11), ("2026-10-13", 3.16)))
    assert data["values"]["HY_OAS_ASOF"] == "Oct 13"


def test_never_goes_backwards():
    data = _issue()
    assert not refresh.apply_hy_oas(data, _hy(("2026-10-09", 3.0), ("2026-10-10", 3.1)))
    assert not refresh.apply_mortgage(data, _weekly(date(2026, 10, 1), 7.0))
    assert data == _issue()


def test_refresh_writes_json_and_chart_only(root, monkeypatch):
    md_before = (root / "issues" / f"{DAY}.md").read_text(encoding="utf-8")
    pub_before = (root / "issues" / "published.json").read_text(encoding="utf-8")
    later = [RatePoint("MORTGAGE30US", date(2026, 10, 22), 9.99)]  # after the issue: ignored
    _fake_fred(monkeypatch, _weekly(date(2026, 10, 15), 7.40) + later,
               _hy(("2026-10-13", 3.16), ("2026-10-14", 3.25), ("2026-10-16", 9.0)))
    changed = refresh.refresh(DAY, root=root, api_key="k", client=object())
    assert changed == ["mortgage 7.40% (Oct 15)", "high-yield spread 3.25% (Oct 14)"]
    data = json.loads((root / "issues" / f"{DAY}.json").read_text(encoding="utf-8"))
    assert data["values"]["MORTGAGE30US"] == "7.40%" and data["values"]["HY_OAS"] == "3.25%"
    assert (root / "issues" / "img" / f"{DAY}-mortgage.png").stat().st_size > 1000
    assert (root / "issues" / f"{DAY}.md").read_text(encoding="utf-8") == md_before
    assert (root / "issues" / "published.json").read_text(encoding="utf-8") == pub_before


def test_refresh_no_change_leaves_files_alone(root, monkeypatch):
    path = root / "issues" / f"{DAY}.json"
    _fake_fred(monkeypatch, _weekly(date(2026, 10, 15), 7.40), _hy(("2026-10-14", 3.25)))
    refresh.refresh(DAY, root=root, api_key="k", client=object())
    first = path.read_text(encoding="utf-8")
    png = root / "issues" / "img" / f"{DAY}-mortgage.png"
    png.unlink()
    assert refresh.refresh(DAY, root=root, api_key="k", client=object()) == []
    assert path.read_text(encoding="utf-8") == first
    assert not png.exists()  # no chart redraw without a data change


def test_no_dashes_in_new_files():
    for p in (Path("src/refresh.py"), REFRESH_WF):
        text = p.read_text(encoding="utf-8")
        assert "—" not in text and "–" not in text, p


def _wf():
    return yaml.safe_load(REFRESH_WF.read_text(encoding="utf-8"))


def test_refresh_workflow_schedule_and_scope():
    wf = _wf()
    on = wf.get("on", wf.get(True))
    assert "workflow_dispatch" in on
    crons = [c["cron"] for c in on["schedule"]]
    assert crons and all(c.endswith("* * 1-5") for c in crons)  # weekdays only
    assert wf["permissions"] == {"contents": "write", "actions": "write"}
    runs = "\n".join(s.get("run", "") for j in wf["jobs"].values() for s in j["steps"])
    assert "python -m src.refresh" in runs
    assert "src.publish" not in runs and "src.main" not in runs
    adds = [ln.strip() for ln in runs.splitlines() if ln.strip().startswith("git add")]
    assert adds and set(adds) == {"git add issues/*.json issues/img/"}
    assert "git status --porcelain issues/*.md issues/published.json" in runs  # guard
    assert "gh workflow run publish.yml" in runs and "-f issue" not in runs
    for job in wf["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), step
