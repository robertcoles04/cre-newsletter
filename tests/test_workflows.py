from pathlib import Path

import pytest
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
    assert set(on) == {"issues", "workflow_dispatch", "push"}
    assert wf["permissions"] == {"contents": "write", "issues": "write",
                                 "pages": "write", "id-token": "write"}
    assert "concurrency" not in wf  # per-job concurrency instead
    assert set(wf["jobs"]) == {"record", "deploy"}


def test_record_job_serialised_per_issue_and_never_cancelled():
    rec = _wf()["jobs"]["record"]
    assert "approved" in rec["if"] and "github.event.issue.title" in rec["if"]
    assert rec["concurrency"] == {
        "group": "record-${{ github.event.issue.number || inputs.issue }}",
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


DAILY = Path(".github/workflows/daily.yml")


def _daily():
    return yaml.safe_load(DAILY.read_text(encoding="utf-8"))


def _on(wf):
    return wf.get("on", wf.get(True))


def _steps(wf):
    return [s for job in wf["jobs"].values() for s in job["steps"]]


def test_dispatch_takes_optional_issue_number():
    inputs = _on(_wf())["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"issue"}
    assert inputs["issue"]["type"] == "string"
    assert inputs["issue"]["required"] is False and inputs["issue"]["default"] == ""


def test_dispatch_with_issue_runs_record_like_an_approval():
    wf = _wf()
    rec, dep = wf["jobs"]["record"], wf["jobs"]["deploy"]
    assert "github.event_name == 'workflow_dispatch' && inputs.issue != ''" in rec["if"]
    assert set(rec["outputs"]) == {"day", "issue", "auto"}
    # a dispatch WITH an issue only deploys after record succeeds (gate passed)
    assert "github.event_name == 'workflow_dispatch' && inputs.issue == ''" in dep["if"]
    close = next(s for s in dep["steps"] if s.get("name") == "Comment and close the Issue")
    assert close["if"] == "needs.record.result == 'success'"
    assert "Published automatically: https://creblurb.org/issues/$DAY/" in close["run"]
    assert "gh issue close" in close["run"]
    gate_fail = next(s for s in rec["steps"]
                     if s.get("if") == "failure() && steps.gate.outcome == 'failure'")
    run = gate_fail["run"]
    assert "Not auto-published: %s. Fix issues/%s.md and add the `approved` label to publish." in run
    auto_branch = run.split('if [ "$AUTO" = "true" ]; then', 1)[1].split("\nfi\n", 1)[0]
    assert "gh issue close" not in auto_branch and "remove-label" not in auto_branch
    assert "exit 0" in auto_branch


def test_push_trigger_rebuilds_only():
    wf = _wf()
    push = _on(wf)["push"]
    assert push["branches"] == ["main"]
    assert "issues/*.md" in push["paths"]
    assert all(p.startswith("issues/") for p in push["paths"])
    rec, dep = wf["jobs"]["record"], wf["jobs"]["deploy"]
    assert "push" not in rec["if"]  # no gate, nothing recorded on push
    assert "github.event_name == 'push'" in dep["if"]
    assert dep["concurrency"] == {"group": "pages", "cancel-in-progress": True}


def test_daily_dispatches_publish_with_issue_number():
    wf = _daily()
    assert wf["permissions"]["actions"] == "write"
    steps = wf["jobs"]["draft"]["steps"]
    names = [s.get("name") for s in steps]
    pipeline = next(s for s in steps if s.get("name") == "Run pipeline")
    assert pipeline["id"] == "pipeline"
    auto = next(s for s in steps if s.get("name") == "Auto-publish the draft")
    # only after issues/ is committed to main, and only when this run delivered an Issue
    assert names.index("Auto-publish the draft") > names.index("Commit issues/ to main")
    assert auto["if"] == "steps.pipeline.outputs.issue != ''"
    assert auto["env"]["ISSUE"] == "${{ steps.pipeline.outputs.issue }}"
    assert "gh workflow run publish.yml" in auto["run"] and '-f issue="$ISSUE"' in auto["run"]
    assert "--ref main" in auto["run"]
    assert "gh issue edit" not in DAILY.read_text(encoding="utf-8")  # label would not trigger


@pytest.mark.parametrize("wf", [PUBLISH, DAILY], ids=["publish", "daily"])
def test_no_expressions_inside_run_scripts(wf):
    """Untrusted values (Issue titles, dispatch inputs) reach scripts only via env."""
    data = yaml.safe_load(wf.read_text(encoding="utf-8"))
    for step in _steps(data):
        run = step.get("run", "")
        assert "${{" not in run, step
        assert "inputs." not in run and "github.event.issue.title" not in run, step
