from pathlib import Path

import yaml

PUBLISH = Path(".github/workflows/publish.yml")


def _wf():
    return yaml.safe_load(PUBLISH.read_text(encoding="utf-8"))


def _uses(job):
    return [s.get("uses", "") for s in job["steps"]]


def test_publish_workflow_shape():
    wf = _wf()
    on = wf.get("on", wf.get(True))
    assert on["issues"]["types"] == ["labeled"] and "workflow_dispatch" in on
    assert wf["permissions"] == {"contents": "write", "issues": "write",
                                 "pages": "write", "id-token": "write"}
    assert "concurrency" not in wf  # per-job concurrency instead
    assert set(wf["jobs"]) == {"record", "deploy"}


def test_record_job_serialised_per_issue_and_never_cancelled():
    rec = _wf()["jobs"]["record"]
    assert "approved" in rec["if"] and "github.event.issue.title" in rec["if"]
    assert rec["concurrency"] == {"group": "record-${{ github.event.issue.number }}",
                                  "cancel-in-progress": False}
    assert rec["outputs"]["day"]
    runs = "\n".join(s.get("run", "") for s in rec["steps"])
    assert "pipefail" in runs and "2>&1 | tee gate.txt" in runs
    assert 'publish: $DAY' in runs and "pull --rebase" in runs
    failure = [s for s in rec["steps"]
               if s.get("if") == "failure() && steps.gate.outcome == 'success'"]
    assert failure and "remove-label approved" in failure[0]["run"]
    assert not any(u.startswith("actions/deploy-pages@") for u in _uses(rec))


def test_deploy_job_runs_after_record_or_on_dispatch():
    dep = _wf()["jobs"]["deploy"]
    assert dep["needs"] == "record"
    assert "always()" in dep["if"] and "workflow_dispatch" in dep["if"]
    assert "needs.record.result == 'success'" in dep["if"]
    assert dep["concurrency"] == {"group": "pages", "cancel-in-progress": True}
    assert dep["environment"]["name"] == "github-pages"
    uses = _uses(dep)
    assert any(u.startswith("actions/upload-pages-artifact@") for u in uses)
    assert any(u.startswith("actions/deploy-pages@") for u in uses)
    checkout = next(s for s in dep["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "main"


def test_issue_title_never_inlined_into_scripts():
    wf = _wf()
    title_in_env = False
    for job in wf["jobs"].values():
        for step in job["steps"]:
            assert "github.event.issue.title" not in step.get("run", ""), step
            title_in_env |= "github.event.issue.title" in str(step.get("env", {}))
    # the title is only allowed as an env value (job `if:` is not a shell)
    assert title_in_env


def test_daily_creates_approved_label():
    assert "gh label create approved" in Path(".github/workflows/daily.yml").read_text(encoding="utf-8")
