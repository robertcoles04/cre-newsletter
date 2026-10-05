"""Chart rendering (matplotlib, no display needed).

Every chart uses the Offering Memo palette from DESIGN.md (navy, gold, ink, muted,
hairline, up/down), a Public Sans-like sans serif with system fallbacks, a white
background, no titles or legends inside the image (the page caption explains it) and
2x resolution. Each chart has a matching *_alt() that writes alt text with the actual
values, filled by code. A chart function returns None when there is too little data.
"""

import logging
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

from src.models import RatePoint  # noqa: E402

# Missing fonts in the fallback list are expected (CI has none of the first few).
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

MAX_POINTS = 30
SCALE = 2  # PNGs are saved at 2x the logical size for sharp phones and retina screens
BASE_DPI = 100  # logical pixels per inch: an 8 x 4 inch figure is an 800 x 400 <img>

NAVY = "#0E2A47"
NAVY_TINT = "#B9C8DA"
GOLD = "#B08D3C"
INK = "#1B2430"
MUTED = "#56616F"
HAIRLINE = "#D9DDE3"
UP = "#1F7A4D"
DOWN = "#B23A3A"
WHITE = "#FFFFFF"

STYLE = {
    "font.family": "sans-serif",
    "font.sans-serif": ["Public Sans", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial",
                        "Liberation Sans", "DejaVu Sans"],
    "font.size": 11,
    "text.color": INK,
    "axes.edgecolor": HAIRLINE,
    "axes.labelcolor": MUTED,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "figure.facecolor": WHITE,
    "axes.facecolor": WHITE,
    "savefig.facecolor": WHITE,
}

REIT_MIN = 3        # skip the REIT scoreboard below this many tickers
MORTGAGE_MIN = 8    # skip the mortgage trend below this many weekly points
CURVE_MIN = 3       # skip the yield curve below this many maturities
PAIR_FIGSIZE = (3.6, 2.6)  # yield curve + mortgage: a 2-up row on desktop (~330px each)
RATE_FIGSIZE = (8.0, 4.0)
FED_FIGSIZE = (8.0, 1.3)
# Phone variants of the full-width charts (served through <picture> under 600px wide):
# narrower figures so their text stays readable when the image is ~343px wide.
NARROW_WIDTH = 4.0
RATE_NARROW = (NARROW_WIDTH, 2.8)
FED_NARROW = (NARROW_WIDTH, 1.5)
NARROW = ("chart", "fed", "reits")  # charts that have a phone variant


def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


def _pct(v: float) -> str:
    return f"{round(v, 1) + 0.0:+.1f}%"


def size_px(figsize: tuple[float, float]) -> tuple[int, int]:
    """Logical <img> width and height for a figure size in inches."""
    return round(figsize[0] * BASE_DPI), round(figsize[1] * BASE_DPI)


def _clean(ax, grid_axis: str = "y") -> None:
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(HAIRLINE)
    ax.tick_params(length=0, labelsize=10, pad=6)
    if grid_axis:
        ax.grid(axis=grid_axis, color=HAIRLINE, linewidth=0.8)
    ax.set_axisbelow(True)


def _save(fig, out: Path, title: str) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, format="png", dpi=BASE_DPI * SCALE, metadata={"Title": title})
    return out


def _pct_axis(ax, decimals: int = 1) -> None:
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.{decimals}f}%"))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4, steps=[1, 2, 5, 10]))


def _date_axis(ax) -> None:
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: _day(mdates.num2date(v).date())))


def _line(points: list[RatePoint], out: Path, title: str, figsize) -> Path:
    """Navy line, gold dot and bold label on the latest value."""
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=figsize, dpi=BASE_DPI)
        try:
            xs, ys = [p.date for p in points], [p.value for p in points]
            decimals = 1
            if points:
                ax.plot(xs, ys, color=NAVY, linewidth=2, solid_capstyle="round")
                ax.plot([xs[-1]], [ys[-1]], "o", color=GOLD, markersize=6, zorder=3)
                falling = len(ys) > 1 and ys[-2] > ys[-1]  # label away from the line
                ax.annotate(f"{ys[-1]:.2f}%", (xs[-1], ys[-1]),
                            xytext=(0, -12 if falling else 10), textcoords="offset points",
                            ha="right", va="top" if falling else "bottom", color=INK,
                            fontsize=10, fontweight="bold",
                            bbox={"boxstyle": "square,pad=0.15", "fc": WHITE, "ec": "none"})
                lo, hi = min(ys), max(ys)
                pad = max((hi - lo) * 0.25, 0.05)
                ax.set_ylim(lo - pad, hi + pad)
                ax.margins(x=0.02)
                _date_axis(ax)
                decimals = 2 if hi - lo < 0.3 else 1
            _clean(ax)
            _pct_axis(ax, decimals)
            fig.tight_layout()
            return _save(fig, out, title)
        finally:
            plt.close(fig)


# ---------------------------------------------------------------- 10-Year Treasury

def rate_chart(points: list[RatePoint], out: Path, title: str, narrow: bool = False) -> Path:
    """Line chart of the last 30 observations (800 x 400 logical, 2x PNG; `narrow` is the
    phone variant). `title` is stored in the PNG metadata; the page caption names it."""
    pts = sorted(points, key=lambda p: p.date)[-MAX_POINTS:]
    return _line(pts, out, title, RATE_NARROW if narrow else RATE_FIGSIZE)


def rate_alt(points: list[RatePoint], name: str = "10-Year Treasury yield") -> str:
    pts = sorted(points, key=lambda p: p.date)[-MAX_POINTS:]
    if not pts:
        return f"Line chart of the {name}."
    first, last = pts[0], pts[-1]
    return (f"Line chart of the {name}, from {first.value:.2f}% on {_day(first.date)} "
            f"to {last.value:.2f}% on {_day(last.date)}.")


# ---------------------------------------------------------------- REIT scoreboard

def _moves(moves: list[dict] | None) -> list[dict]:
    ok = []
    for m in moves or []:
        try:
            chg = float(m.get("chg_pct"))
        except (TypeError, ValueError):
            continue
        if m.get("ticker") and chg == chg:  # chg == chg drops NaN
            ok.append({**m, "chg_pct": chg})
    return sorted(ok, key=lambda m: -m["chg_pct"])


def reit_label(m: dict) -> str:
    """"PLD · warehouse": ticker, a middle dot, the short property type."""
    kind = (m.get("type") or "").strip()
    return f"{m['ticker']} · {kind}" if kind else m["ticker"]


def reit_figsize(moves: list[dict] | None, narrow: bool = False) -> tuple[float, float]:
    return (NARROW_WIDTH if narrow else 8.0, round(0.5 + 0.34 * len(_moves(moves)), 2))


def reit_scoreboard(moves: list[dict] | None, out: Path, narrow: bool = False) -> Path | None:
    """Horizontal bars of each REIT's daily % move, best on top, green up / red down.
    None when fewer than REIT_MIN tickers have a move."""
    rows = _moves(moves)
    if len(rows) < REIT_MIN:
        return None
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=reit_figsize(rows, narrow), dpi=BASE_DPI)
        try:
            ys = list(range(len(rows)))[::-1]  # best at the top
            vals = [m["chg_pct"] for m in rows]
            colors = [UP if v > 0 else DOWN if v < 0 else MUTED for v in vals]
            ax.barh(ys, vals, color=colors, height=0.62)
            ax.set_yticks(ys, [reit_label(m) for m in rows])
            span = max(max(abs(v) for v in vals), 0.5)
            pad = span * (0.55 if narrow else 0.3)  # room for the value labels
            ax.set_xlim(min(min(vals), 0) - pad, max(max(vals), 0) + pad)
            for y, v in zip(ys, vals):
                ax.annotate(_pct(v), (v, y), xytext=(5 if v >= 0 else -5, 0),
                            textcoords="offset points", va="center",
                            ha="left" if v >= 0 else "right", fontsize=10, color=INK)
            ax.axvline(0, color=MUTED, linewidth=0.9)
            _clean(ax, grid_axis="")
            ax.tick_params(axis="y", labelsize=10.5, labelcolor=INK)
            ax.spines["bottom"].set_visible(False)
            ax.set_xticks([])
            ax.set_ylim(-0.6, len(rows) - 0.4)
            fig.tight_layout()
            return _save(fig, out, "REIT daily moves")
        finally:
            plt.close(fig)


def reit_alt(moves: list[dict] | None) -> str:
    rows = _moves(moves)
    listed = ", ".join(f"{m['ticker']} {_pct(m['chg_pct'])}" for m in rows)
    return f"Bar chart of today's move for {len(rows)} REITs, best to worst: {listed}."


# ---------------------------------------------------------------- Yield curve

Curve = list[tuple[str, float | None, float | None]]  # (maturity, today %, month-ago %)


def curve_rows(curve: Curve) -> Curve:
    return [(lbl, now, ago) for lbl, now, ago in curve or [] if now is not None]


def yield_curve(curve: Curve, out: Path) -> Path | None:
    """Treasury yields by maturity: today (navy, labeled) vs about a month ago (navy
    tint). `curve` is in maturity order. None below CURVE_MIN maturities with today's
    yield."""
    rows = curve_rows(curve)
    if len(rows) < CURVE_MIN:
        return None
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=PAIR_FIGSIZE, dpi=BASE_DPI)
        try:
            xs = list(range(len(rows)))
            now = [r[1] for r in rows]
            ago = [(x, r[2]) for x, r in zip(xs, rows) if r[2] is not None]
            ago_at = dict(ago)
            if len(ago) >= 2:
                ax.plot([a[0] for a in ago], [a[1] for a in ago], color=NAVY_TINT,
                        linewidth=2, marker="o", markersize=4)
            ax.plot(xs, now, color=NAVY, linewidth=2.2, marker="o", markersize=5, zorder=3)
            for x, v in zip(xs, now):
                below = x in ago_at and ago_at[x] > v  # label on the side away from the old line
                ax.annotate(f"{v:.2f}%", (x, v), xytext=(0, -15 if below else 8),
                            textcoords="offset points", ha="center", fontsize=9.5,
                            color=INK, fontweight="bold")
            # Key above the plot: a short line swatch + word for each series, no legend box.
            keys = [("Today", NAVY, INK)] + ([("A month ago", NAVY_TINT, MUTED)]
                                             if len(ago) >= 2 else [])
            x0 = 0.0
            for word, line, ink in keys:
                ax.plot([x0, x0 + 0.06], [1.1, 1.1], transform=ax.transAxes, color=line,
                        linewidth=2.2, clip_on=False, solid_capstyle="round")
                ax.text(x0 + 0.08, 1.1, word, transform=ax.transAxes, color=ink,
                        fontsize=9.5, va="center")
                x0 += 0.36  # room for the swatch and "Today" at this figure width
            ax.set_xticks(xs, [r[0] for r in rows])
            every = now + [a[1] for a in ago]
            lo, hi = min(every), max(every)
            pad = max((hi - lo) * 0.35, 0.12)
            ax.set_ylim(lo - pad, hi + pad)
            ax.set_xlim(-0.35, len(rows) - 0.65)
            _clean(ax)
            _pct_axis(ax)
            fig.tight_layout()
            return _save(fig, out, "Treasury yield curve")
        finally:
            plt.close(fig)


def curve_alt(curve: Curve, ago_date: date | None = None) -> str:
    rows = curve_rows(curve)
    now = ", ".join(f"{lbl} {v:.2f}%" for lbl, v, _ in rows)
    text = f"Line chart of Treasury yields by maturity. Today: {now}."
    ago = [(lbl, a) for lbl, _, a in rows if a is not None]
    if ago:
        when = f" ({_day(ago_date)})" if ago_date else ""
        text += f" A month ago{when}: " + ", ".join(f"{lbl} {a:.2f}%" for lbl, a in ago) + "."
    return text


# ---------------------------------------------------------------- Fed odds bar

# (key, label, fill, text color inside the segment): navy shades and muted, no new hues.
FED_PARTS = (("cut", "Cut", NAVY, WHITE), ("hold", "Hold", NAVY_TINT, INK),
             ("hike", "Hike", MUTED, WHITE))


def fed_parts(odds: dict) -> list[tuple[str, float, str, str]]:
    """[(label, pct, fill, text color)] for the buckets that have a number."""
    out = []
    for key, label, fill, ink in FED_PARTS:
        try:
            pct = float(str(odds.get(key)).strip().rstrip("%"))
        except (TypeError, ValueError):
            continue
        out.append((label, pct, fill, ink))
    return out


def fed_odds_bar(odds: dict, out: Path, narrow: bool = False) -> Path | None:
    """One stacked bar: cut / hold / hike, labeled inside wide segments and in a key row
    under the bar (so a thin segment still shows its number). `odds` maps cut/hold/hike
    to strings like "15.0%". None when no bucket has a number."""
    parts = fed_parts(odds)
    total = sum(p[1] for p in parts)
    if not parts or total <= 0:
        return None
    with plt.rc_context(STYLE):
        size = FED_NARROW if narrow else FED_FIGSIZE
        fig, ax = plt.subplots(figsize=size, dpi=BASE_DPI)
        try:
            left = 0.0
            for label, pct, fill, ink in parts:
                width = pct / total * 100
                ax.barh([0], [width], left=left, color=fill, height=0.62,
                        edgecolor=WHITE, linewidth=1.5)
                if width / 100 * size[0] >= 1.15:  # inside label only if it fits (inches)
                    ax.text(left + width / 2, 0, f"{label} {pct:.1f}%", ha="center",
                            va="center", fontsize=10.5, color=ink, fontweight="bold")
                left += width
            n = len(parts)
            for i, (label, pct, fill, _) in enumerate(parts):
                x = i / n * 100 + (2 if narrow else 9)
                ax.scatter([x], [-0.82], marker="s", s=70, color=fill,
                           edgecolors=HAIRLINE, linewidths=0.6, clip_on=False)
                ax.text(x + (4 if narrow else 2), -0.82, f"{label} {pct:.1f}%", va="center",
                        ha="left",
                        fontsize=10.5, color=INK)
            ax.set_xlim(0, 100)
            ax.set_ylim(-1.15, 0.45)
            ax.axis("off")
            fig.subplots_adjust(left=0.01, right=0.99, top=0.98, bottom=0.04)
            return _save(fig, out, "Fed meeting odds")
        finally:
            plt.close(fig)


def fed_alt(odds: dict, meeting: str | None = None) -> str:
    when = f" for the {meeting} Fed meeting" if meeting else ""
    listed = ", ".join(f"{label.lower()} {pct:.1f}%" for label, pct, _, _ in fed_parts(odds))
    return f"Stacked bar of prediction-market odds{when}: {listed}."


# ---------------------------------------------------------------- 30-year mortgage

MORTGAGE_WEEKS = 26


def mortgage_trend(points: list[RatePoint], out: Path) -> Path | None:
    """Small line of the last 26 weekly 30-year mortgage rates. None below MORTGAGE_MIN."""
    pts = sorted(points, key=lambda p: p.date)[-MORTGAGE_WEEKS:]
    if len(pts) < MORTGAGE_MIN:
        return None
    return _line(pts, out, "30-year mortgage rate", PAIR_FIGSIZE)


def mortgage_alt(points: list[RatePoint]) -> str:
    pts = sorted(points, key=lambda p: p.date)[-MORTGAGE_WEEKS:]
    return rate_alt(pts, "30-year mortgage rate over the last six months")
