"""Market Watch (weekday section): one story each for the Sun Belt, the West Coast and
International.

A story's region comes from a keyword match on its title (metros, states, countries),
then its summary, then the tag of the regional Google News query that found it
(config/sources.yaml `region:`). That way a Dallas story from a general feed still counts.
"""

import re

REGIONS = ("sun_belt", "west_coast", "international")
HEADINGS = {"sun_belt": "Sun Belt", "west_coast": "West Coast", "international": "International"}


def _words(*names: str) -> re.Pattern:
    # Lookarounds instead of \b so names ending in "." ("L.A.", "U.K.") still match.
    return re.compile(r"(?<!\w)(?:" + "|".join(names) + r")(?!\w)", re.I)


KEYWORDS = {
    "sun_belt": _words(
        # "Georgia" is left out (also a country); "Atlanta" covers it.
        "Texas", "Florida", "Arizona", "North Carolina", "South Carolina",
        "Carolinas?", "Tennessee", "Dallas", "Fort Worth", "DFW", "Houston", "Austin",
        "San Antonio", "Miami", "Fort Lauderdale", "Tampa", "Orlando", "Jacksonville",
        "Atlanta", "Phoenix", "Scottsdale", "Nashville", "Charlotte", "Raleigh", "Durham"),
    "west_coast": _words(
        "Los Angeles", r"L\.A\.", "San Francisco", "Bay Area", "Silicon Valley", "San Jose",
        "Oakland", "San Diego", "Orange County", "Seattle", "Bellevue",
        r"Portland, Ore\.?", "Portland, Oregon",
        "California", "Oregon"),
    "international": _words(
        "London", r"U\.K\.", "UK", "Britain", "British", "Europe", "European", "EU",
        "Germany", "Frankfurt", "Berlin", "Paris", "France", "Ireland", "Dublin", "Spain",
        "Madrid", "Netherlands", "Amsterdam", "Italy", "Milan", "Switzerland", "Zurich",
        "Asia", "Asia-Pacific", "APAC", "Tokyo", "Japan", "China", "Shanghai", "Beijing",
        "Hong Kong", "Singapore", "Seoul", "South Korea", "Australia", "Sydney",
        "Melbourne", "India", "Mumbai", "Canada", "Canadian", "Toronto", "Vancouver",
        "Montreal", "Dubai", "UAE", "Saudi", "Riyadh", "Middle East", r"(?<!New )Mexico",
        "Mexico City", "Latin America", "Brazil", "Sao Paulo", "São Paulo"),
}


def _match(text: str) -> str | None:
    hits = [(m.start(), region) for region, pat in KEYWORDS.items()
            if (m := pat.search(text or ""))]
    return min(hits)[1] if hits else None  # earliest mention wins


def region_of(title: str, summary: str = "", tag: str | None = None) -> str | None:
    """Region for a story: title keywords, then summary keywords, then the query tag."""
    found = _match(title) or _match(summary)
    if found:
        return found
    return tag if tag in REGIONS else None


_DOMAIN_OUTLET = re.compile(r"[\w-]+\.(?:com|net|org|co|io)(?:\s+[A-Z][\w.]*)?")
_SUFFIX_OUTLET = re.compile(r"\s+[-|–—]\s+[^-|–—]+$")


def _strip_outlet(text: str) -> str:
    """Drop outlet names ("Investing.com Canada", " - The Real Deal") before place matching."""
    return _SUFFIX_OUTLET.sub("", _DOMAIN_OUTLET.sub(" ", text or ""))


def names_foreign_place(title: str, summary: str = "") -> bool:
    """True when the title or summary itself names a non-US place. The outlet name does
    not count ("Investing.com Canada" is a source, not a place in the story)."""
    pat = KEYWORDS["international"]
    return bool(pat.search(_strip_outlet(title)) or pat.search(_strip_outlet(summary)))


def pick(rows: list, taken_urls: set[str]) -> dict:
    """{region: row or None}. `rows` must already be in rank order; a story already used
    elsewhere in the issue (taken_urls) or for another region is skipped."""
    out = {r: None for r in REGIONS}
    used = set(taken_urls)
    for row in rows:
        if row["url"] in used:
            continue
        keys = row.keys() if hasattr(row, "keys") else row
        region = region_of(row["title"], row["summary"] or "",
                           row["region"] if "region" in keys else None)
        if region == "international" and not names_foreign_place(
                row["title"], row["summary"] or ""):
            region = None  # the regional query tag alone is not enough
        if region and out[region] is None:
            out[region] = row
            used.add(row["url"])
    return out
