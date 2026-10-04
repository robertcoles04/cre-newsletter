"""Rate chart rendering (matplotlib, no display needed)."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from src.models import RatePoint  # noqa: E402

MAX_POINTS = 30


def rate_chart(points: list[RatePoint], out: Path, title: str) -> Path:
    """Line chart of the last 30 observations, saved as an 800x400 PNG."""
    out = Path(out)
    pts = sorted(points, key=lambda p: p.date)[-MAX_POINTS:]
    fig, ax = plt.subplots(figsize=(8, 4), dpi=100)
    try:
        if pts:
            ax.plot([p.date for p in pts], [p.value for p in pts],
                    color="#1f4e79", linewidth=2)
        ax.set_title(title)
        ax.set_ylabel("%")
        ax.grid(True, alpha=0.3)
        fig.autofmt_xdate()
        fig.tight_layout()
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, format="png")
    finally:
        plt.close(fig)
    return out
