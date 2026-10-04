import subprocess

import pytest

from src import llm
from src.llm import LLMError, run_claude


def test_timeout_raises_llmerror(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("claude", 600)
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(LLMError):
        run_claude("hi", "sonnet")


def test_missing_cli_raises_llmerror(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(LLMError):
        run_claude("hi", "sonnet")


def test_nonzero_exit_raises_llmerror(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 2, stdout="", stderr=""))
    with pytest.raises(LLMError, match="exit 2"):
        run_claude("hi", "sonnet")


def test_nonzero_exit_uses_stderr(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, stdout="", stderr="bad token"))
    with pytest.raises(LLMError, match="bad token"):
        run_claude("hi", "sonnet")


def test_success_returns_stdout(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, stdout="OK", stderr=""))
    assert run_claude("hi", "sonnet") == "OK"
