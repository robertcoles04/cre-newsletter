"""Cross-day dedupe by deal, not just by URL or exact headline. The headlines are the real
ones behind the EQT / Stockbridge and Ortega / One North Wacker repeats of 2026-10-15
through 2026-10-19, where each day a different outlet covered the same deal."""

from datetime import date, datetime, timedelta, timezone

from src.config import ET
from src.factsheet import build_factsheet
from src.dedupe import common_words, same_deal
from src.store import connect, record_used_stories, title_key

# Used in the 2026-10-15 and 2026-10-16 issues.
USED_EQT_1015 = ("https://www.connectcre.com/stories/eqt-to-acquire-stockbridge-adding-us16-5b-in-aum/",
                 "EQT to Acquire Stockbridge, Adding US$16.5B in AUM")
USED_ZARA_1015 = ("https://www.bisnow.com/news/chicago/office/chicago-office-tower-sells-for-550m-to-zara-founder-amancio-ortega",
                  "Chicago Office Tower Sells For $550M To Zara Founder In Blockbuster Deal")
USED_EQT_1016 = ("https://commercialobserver.com/2026/10/eqt-real-estate-acquires-stockbridge-375m/",
                 "EQT Real Estate Acquires $16B Alternatives Investment Firm Stockbridge for $375M")
USED_ORTEGA_1016 = ("https://news.google.com/rss/articles/ortega-trd",
                    "Zara founder Amancio Ortega closes on 1 North Wacker for $550M - The Real Deal")

# Picked again for the original 2026-10-19 draft (git show bb14da8:issues/2026-10-19.md).
NEW_EQT = ("https://news.google.com/rss/articles/eqt-trd",
           "EQT Acquiring Stockbridge Capital for Up to $550M - The Real Deal")
NEW_ORTEGA = ("https://news.google.com/rss/articles/ortega-globest",
              "Ortega Buys Chicago's One North Wacker for $550M - Globest")

RUN = date(2026, 10, 19)


def k(title):
    return title_key(title)


def test_new_outlet_headline_matches_used_deal():
    assert same_deal(k(NEW_EQT[1]), k(USED_EQT_1015[1]))
    assert same_deal(k(NEW_EQT[1]), k(USED_EQT_1016[1]))
    assert same_deal(k(NEW_ORTEGA[1]), k(USED_ORTEGA_1016[1]))
    # One shared name plus the same dollar amount is enough.
    assert same_deal(k("Swedish Real Estate Giant To Buy Stockbridge For Up To $550M - Yahoo Finance"),
                     k(NEW_EQT[1]))


def test_different_deals_are_not_matched():
    # Same company, different deal and amount.
    assert not same_deal(k("Stockbridge Buys Maryland Industrial Property for $57M"),
                         k(USED_EQT_1015[1]))
    assert not same_deal(k("Stockbridge Buys Maryland Industrial Property for $57M"),
                         k(USED_EQT_1016[1]))
    # Same city and property type, different deal.
    assert not same_deal(k("Chicago Office Tower Sells For $90M"), k(USED_ZARA_1015[1]))
    # A two-word name (one entity) is not two shared names.
    assert not same_deal(k("Cushman & Wakefield Arranges $40M Loan for Dallas Office"),
                         k("Cushman & Wakefield Brokers Sale of Miami Retail Center"))
    assert not same_deal(k("Ortega Buys Chicago's One North Wacker for $550M"), "")


def test_words_common_across_headlines_are_not_names():
    terra = k("Terra-Led JV Lands $507M Construction Loan for South Beach Condo")
    other = k("Intracorp Nabs $173M Construction Loan From BGO for Austin Condo Building")
    filler = [f"story {i} about the cmbs desk and a buyer named zz{i}" for i in range(200)]
    common = common_words(filler + [terra, other])
    assert "desk" in common and "zz7" not in common
    assert not same_deal(other, terra)  # "construction loan ... condo" is not a name
    a, b = "kbra desk rates gamma tower", "kbra desk flags gamma tower plan"
    assert same_deal(a, b)
    assert not same_deal(a, b, frozenset({"gamma"}))  # a common word does not count
    # A deal name that got a burst of coverage is still a name in a big corpus.
    assert same_deal(k(NEW_EQT[1]), k(USED_EQT_1015[1]),
                     common_words(filler + [k(NEW_EQT[1])] * 6))


def add(conn, url, title, hours_before=2, section="top", importance=9):
    pub = (datetime(2026, 10, 19, 5, 0, tzinfo=ET) - timedelta(hours=hours_before))
    canon = url.split("//")[1].removeprefix("www.").rstrip("/")
    cur = conn.execute(
        "INSERT INTO items (source, url, canonical_url, title, published_at, summary,"
        " priority, also_covered, section, importance) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("Src", url, canon, title,
         pub.astimezone(timezone.utc).isoformat(timespec="seconds"),
         "sum", 50, "[]", section, importance))
    conn.execute("UPDATE items SET cluster_id = id WHERE id = ?", (cur.lastrowid,))
    conn.commit()


def used(conn, day, stories, with_titles=True):
    md = " ".join(f"[x]({u})" for u, _ in stories)
    fs = {"top": [{"url": u, "title": t} for u, t in stories]} if with_titles else None
    record_used_stories(conn, day, md, fs)


def picked(conn):
    s = build_factsheet(conn, RUN, None, [], [], None)
    return {x["title"] for key in ("top", "quick_hits") for x in s[key]}


def test_same_deal_from_new_outlet_is_filtered_before_drafting():
    conn = connect(":memory:")
    used(conn, date(2026, 10, 15), [USED_EQT_1015, USED_ZARA_1015])
    used(conn, date(2026, 10, 16), [USED_EQT_1016, USED_ORTEGA_1016])
    add(conn, *NEW_EQT)
    add(conn, *NEW_ORTEGA)
    add(conn, "https://a.com/maryland", "Stockbridge Buys Maryland Industrial Property for $57M")
    add(conn, "https://a.com/other", "Blackstone Sells Phoenix Apartment Portfolio", importance=3)
    assert picked(conn) == {"Stockbridge Buys Maryland Industrial Property for $57M",
                            "Blackstone Sells Phoenix Apartment Portfolio"}


def test_used_rows_without_titles_fall_back_to_the_item_headline():
    # The 2026-10-17 issue was recorded from its Markdown only (no titles in used_stories).
    conn = connect(":memory:")
    add(conn, *USED_EQT_1016, hours_before=70)  # the original item is still in the DB
    used(conn, date(2026, 10, 17), [USED_EQT_1016], with_titles=False)
    add(conn, *NEW_EQT)
    add(conn, "https://a.com/other", "Blackstone Sells Phoenix Apartment Portfolio", importance=3)
    assert picked(conn) == {"Blackstone Sells Phoenix Apartment Portfolio"}
