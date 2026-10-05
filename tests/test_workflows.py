from pathlib import Path

import yaml

PUBLISH = Path(".github/workflows/publish.yml")


def test_publish_workflow_shape():
    wf = yaml.safe_load(PUBLISH.read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))
    assert on["issues"]["types"] == ["labeled"] and "workflow_dispatch" in on
    assert wf["permissions"] == {"contents": "write", "issues": "write",
                                 "pages": "write", "id-token": "write"}
    job = next(iter(wf["jobs"].values()))
    assert job["environment"]["name"] == "github-pages"
    uses = [s.get("uses", "") for s in job["steps"]]
    assert any(u.startswith("actions/upload-pages-artifact@") for u in uses)
    assert any(u.startswith("actions/deploy-pages@") for u in uses)


def test_issue_title_never_inlined_into_scripts():
    wf = yaml.safe_load(PUBLISH.read_text(encoding="utf-8"))
    job = next(iter(wf["jobs"].values()))
    for step in job["steps"]:
        assert "github.event.issue.title" not in step.get("run", ""), step
    # the title is only allowed as an env value (job `if:` is not a shell)
    steps_with_title = [s for s in job["steps"]
                        if "github.event.issue.title" in str(s.get("env", {}))]
    assert steps_with_title


def test_daily_creates_approved_label():
    assert "gh label create approved" in Path(".github/workflows/daily.yml").read_text(encoding="utf-8")
