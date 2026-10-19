"""Same-deal matching for the cross-day repeat check. A different outlet covering a deal we
already ran has a new URL and a new headline, so URL and exact-title checks miss it. Two
headlines are the same deal when they share the deal's names (companies, people,
buildings), not just common words like "CMBS" or "office". All inputs are title keys
(store.title_key: lowercase, punctuation dropped, outlet suffix removed)."""

import re
from collections import Counter
from collections.abc import Iterable

from rapidfuzz import fuzz

NEAR_IDENTICAL = 85
# A word in at least this share of recent headlines (and at least COMMON_MIN of them) is a
# common word, not a name: "cmbs", "construction", "beach". One deal rarely gets more than
# a handful of headlines (EQT / Stockbridge got 6 in four weeks of about 4,200).
COMMON_SHARE = 0.003
COMMON_MIN = 12

# Words that say what kind of deal a headline is about, not which deal: verbs, property
# types, deal words, places and filler. The frequency check above catches the rest.
_GENERIC = frozenset("""
a an the and or of in on at by to for with from into over as its it is be are was were
after amid near than that this his her their up new us out off via per vs not no
acquire acquires acquired acquiring acquisition acquisitions buy buys buying bought buyer
buyers sell sells sold selling seller sellers sale sales close closes closed closing deal
deals pay pays paid agree agrees agreed plan plans planned nab nabs land lands grab grabs
ink inks sign signs signed lease leases leased leasing refinance refinances refinanced
refinancing refi refis recapitalize recap loan loans lend lends lender lenders lending
financing finance debt equity fund funds raise raises raised capital partners partner
group investment investments investor investors invest invests firm firms company
companies management manager managers advisors adviser properties property real estate
realty reit reits trust office offices tower towers building buildings industrial
warehouse warehouses logistics retail multifamily apartment apartments housing hotel
hotels portfolio portfolios campus center centre plaza park mall data development
developer developers project projects site units unit homes home square feet sf million
billion aum gross asset assets value sector sectors boost boosts expand expands adding
add adds global giant founder ceo billionaire record blockbuster major largest big
biggest top first year years deliver delivers complete completes completed launch
launches opens open report reports market markets arranges arranged brokers brokered
secures secured scores wins tenant tenants space mixed use local family cmbs cre clo
commercial rate rates interest price prices pricing special servicing servicer bridge
construction condo condos luxury delinquency distress
what when where why how who which while about against through could would will may
might can should says said more most less still just again now here there what more
warning warns warn details detail stops stop points point basis rose rise rises rising
fell fall falls drop drops hit hits gets get takes take makes make sees see shows show
lessons lesson inside behind beyond today week weeks month months day days time timeline
downtown midtown uptown north south east west street avenue drive road boulevard one two
three four five chicago york manhattan brooklyn queens bronx angeles san francisco diego
jose miami dallas houston austin atlanta boston seattle denver phoenix nashville charlotte
orlando tampa philadelphia washington texas florida california jersey city county metro
swedish spanish british german canadian american european asian london
""".split())
_AMOUNT = re.compile(r"\b(\d+)(?: (\d{1,2}))? ?(m|mn|million|b|bn|billion)\b")


def _candidates(tokens: Iterable[str]) -> set[str]:
    return {t for t in tokens
            if len(t) >= 3 and t not in _GENERIC and not re.fullmatch(r"\d+[a-z]*", t)}


def common_words(keys: Iterable[str]) -> frozenset[str]:
    """Words frequent enough across these headlines to say nothing about which deal."""
    keys = list(keys)
    df = Counter()
    for key in keys:
        df.update(_candidates(key.split()))
    floor = max(COMMON_MIN, COMMON_SHARE * len(keys))
    return frozenset(w for w, n in df.items() if n >= floor)


def _amounts(key: str) -> set[float]:
    """Dollar amounts in a title key, in millions ("16 5b" is $16.5B, the key's form)."""
    out = set()
    for whole, dec, unit in _AMOUNT.findall(key):
        value = float(f"{whole}.{dec}" if dec else whole)
        out.add(round(value * (1000 if unit.startswith("b") else 1), 1))
    return out


def _runs(tokens: list[str], shared: set[str]) -> int:
    """Shared names counted as entities: "cushman wakefield" side by side is one name."""
    return sum(1 for i, t in enumerate(tokens)
               if t in shared and (i == 0 or tokens[i - 1] not in shared))


def same_deal(a: str, b: str, common: frozenset[str] = frozenset()) -> bool:
    """True when two title keys look like the same deal told by different outlets: two
    separate shared names (EQT + Stockbridge, Ortega + Wacker), or one shared name plus the
    same dollar amount, or a near-identical headline."""
    if not a or not b:
        return False
    if fuzz.token_sort_ratio(a, b) >= NEAR_IDENTICAL:
        return True
    ta, tb = a.split(), b.split()
    shared = (_candidates(ta) & _candidates(tb)) - common
    if not shared:
        return False
    entities = min(_runs(ta, shared), _runs(tb, shared))
    return entities >= 2 or bool(_amounts(a) & _amounts(b))
