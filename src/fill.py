"""Fill {{PLACEHOLDERS}} with pre-formatted values. Unknown names become "n/a"."""

import re

NA = "n/a"
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def fill(md: str, values: dict) -> tuple[str, list[str]]:
    """Return (filled markdown, unknown placeholder names in first-seen order)."""
    missing: list[str] = []

    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name in values:
            return str(values[name])
        if name not in missing:
            missing.append(name)
        return NA

    return PLACEHOLDER.sub(sub, md), missing
