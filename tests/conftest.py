import pytest
"""Pytest configuration and fixtures."""

import os
from pathlib import Path


# Ensure tests run from the project root
os.chdir(Path(__file__).parent.parent)


@pytest.fixture(autouse=True)
def _isolate_issue_files(tmp_path_factory, monkeypatch):
    """Term rotation reads published issue files; keep tests off the real issues/."""
    from src import factsheet
    monkeypatch.setattr(factsheet, "ISSUES_DIR", tmp_path_factory.mktemp("issues"))
