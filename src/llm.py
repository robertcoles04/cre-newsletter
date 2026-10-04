"""Headless Claude runner (`claude -p`). Auth comes from CLAUDE_CODE_OAUTH_TOKEN in CI."""

import subprocess

MODEL_FAST = "sonnet"
MODEL_WRITE = "opus"


class LLMError(RuntimeError):
    pass


def run_claude(prompt: str, model: str) -> str:
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", model, "--output-format", "text"],
            input=prompt, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"claude timed out after {e.timeout}s") from e
    except FileNotFoundError as e:
        raise LLMError("claude CLI not found on PATH") from e
    if proc.returncode != 0:
        raise LLMError(proc.stderr)
    return proc.stdout
