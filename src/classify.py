"""Tag stored stories with a section, asset class, market and importance."""

import json
import re

from src import llm

BATCH_SIZE = 40
SECTIONS = {"debt", "top", "ai", "deal", "other"}
ASSET_CLASSES = {"multifamily", "industrial", "office", "retail", "hospitality",
                 "alternatives", "mixed", "none"}

_DEBT = re.compile(r"CMBS|loan|refinanc|lender|delinquen|foreclos", re.I)
_AI = re.compile(r"\bAI\b|artificial intelligence|proptech", re.I)

PROMPT = """You are classifying commercial real estate news stories for a daily newsletter.
Each story below is one JSON object per line with id, source, title, summary.

For every story return an object with:
- "id": the story id, unchanged
- "section": one of debt, top, ai, deal, other ("ai" = AI tools and use cases in real
  estate: underwriting, lease abstraction, valuation, property management, leasing
  chatbots, proptech launches, brokerages adopting AI; data center or power-grid stories
  may be "ai" too, but they are not the focus)
- "asset_class": one of multifamily, industrial, office, retail, hospitality, alternatives, mixed, none
- "market": the metro area the story is about (e.g. "Dallas"), or "national"
- "importance": an integer from 1 (trivial) to 10 (must-read)

Return ONLY a JSON array of these objects. No prose, no code fences.

Stories:
{stories}
"""


def heuristic(title: str, summary: str = "") -> dict:
    text = f"{title or ''} {summary or ''}"
    if _DEBT.search(text):
        section = "debt"
    elif _AI.search(text):
        section = "ai"
    else:
        section = "top"
    return {"section": section, "asset_class": "none", "market": "national",
            "importance": 5}


def _parse(reply: str, valid_ids: set) -> dict:
    """Return {id: fields} for valid rows in the first JSON array of the reply."""
    start = reply.find("[")
    if start == -1:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(reply[start:])
    except ValueError:
        return {}
    if not isinstance(data, list):
        return {}
    out = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        id_, imp, market = row.get("id"), row.get("importance"), row.get("market")
        section, asset = row.get("section"), row.get("asset_class")
        if (isinstance(id_, bool) or not isinstance(id_, int)
                or not isinstance(section, str) or not isinstance(asset, str)
                or not isinstance(market, str)
                or isinstance(imp, bool) or not isinstance(imp, int)
                or id_ not in valid_ids
                or section not in SECTIONS or asset not in ASSET_CLASSES):
            continue
        out[id_] = {"section": row["section"], "asset_class": row["asset_class"],
                    "market": market.strip()[:60] or "national",
                    "importance": max(1, min(10, imp))}
    return out


def classify(conn, run=llm.run_claude) -> int:
    rows = conn.execute(
        "SELECT id, source, title, summary FROM items"
        " WHERE section IS NULL AND id = cluster_id ORDER BY id").fetchall()
    done = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        stories = "\n".join(json.dumps(
            {"id": r["id"], "source": r["source"], "title": r["title"],
             "summary": (r["summary"] or "")[:300]}) for r in batch)
        try:
            tags = _parse(run(PROMPT.format(stories=stories), llm.MODEL_FAST),
                          {r["id"] for r in batch})
        except llm.LLMError:
            tags = {}
        for r in batch:
            t = tags.get(r["id"]) or heuristic(r["title"], r["summary"])
            conn.execute(
                "UPDATE items SET section=?, asset_class=?, market=?, importance=?"
                " WHERE id=?",
                (t["section"], t["asset_class"], t["market"], t["importance"], r["id"]))
            done += 1
        conn.commit()
    return done
