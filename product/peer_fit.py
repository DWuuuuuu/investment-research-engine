"""Does a stock actually belong to the industry its vendor assigns it?

WHY THIS EXISTS
---------------
Peer lists were built from Yahoo's industry classification alone. For most
companies that is fine. For a whole class of newer businesses it is badly
wrong, and the card then compares a company with strangers:

    IREN   -> "Capital Markets"  -> Morgan Stanley, Goldman Sachs, Schwab
    MARA   -> "Capital Markets"  -> Morgan Stanley, Goldman Sachs, Schwab
    WULF   -> "Capital Markets"  -> Morgan Stanley, Goldman Sachs, Schwab
    CORZ   -> "Software - Infrastructure" -> Microsoft, Oracle, Palo Alto
    NBIS   -> "Internet Content" -> Alphabet, Meta, Snap, Pinterest, Match

Every one of those is a bitcoin miner turned AI/HPC data-centre operator. They
are each other's peers and nobody else's.

THE TEST
--------
Two independent things Yahoo publishes are played off against each other:

  1. the industry cohort (`Industry(key).top_companies`), and
  2. the co-viewed set ("people who viewed this also viewed"), which is
     behavioural and owes nothing to the taxonomy.

Each candidate's business description (`longBusinessSummary`) is compared with
the subject's by TF-IDF cosine, and the two groups' median similarity decides:

    the industry cohort is REJECTED when its median similarity is both
    low outright (< 0.08) and clearly worse than the co-viewed set's (< 0.6x).

Both conditions matter. The ratio alone would fire on mega-caps, whose
co-viewed set is just other mega-caps (KO is co-viewed with JNJ, DIS and JPM
but belongs with PEP). The floor alone would fire wherever descriptions are
written in dissimilar prose.

CALIBRATION (23 US tickers, 2026-09-17). Rejected, correctly: WULF 0.07,
IREN 0.16, MARA 0.32, CORZ 0.34, NBIS 0.52 (ratios). Kept, correctly: AAPL
0.67, COIN 0.90, MSFT 0.99, JPM 0.84, KO 6.02, and every name whose cohort
median cleared 0.08 (XOM, UNH, COST, CAT, TSLA, TSM, AVGO, LMT, NEE, SMCI,
VRT, HOOD, PLTR, JNJ, PLD). The decision boundary sits in an empty band
between 0.52 and 0.66.

WHAT REPLACES A REJECTED COHORT
-------------------------------
The co-viewed graph one hop out: the subject's co-viewed names plus theirs.
For IREN that is HUT, WULF, CRWV, KEEL, CIFR, APLD, CORZ, NBIS - the actual
cohort. Candidates too far from the subject's description are dropped (ONDS,
a drone maker co-viewed for momentum, not business), and what remains is
ranked by size like any other peer list.

This never runs for A-shares: Tushare's industry classification is assigned by
the exchange, not inferred, and does not show this failure mode.
"""
from __future__ import annotations

import json
import math
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_TTL_RELATED = 24 * 3600
_TTL_PROFILE = 7 * 24 * 3600      # a business description changes about never

FIT_FLOOR = 0.08                  # cohort median below this is not a business match
FIT_RATIO = 0.60                  # ... and must be this much worse than co-viewed
KEEP_FRACTION = 0.30              # replacement candidates: fraction of the best score
KEEP_FLOOR = 0.02
STRONG_FRACTION = 0.50            # ... or this much of it, to rank ahead of the tail
MIN_CITATIONS = 2                 # floor for "named by many neighbours" (a majority, if more)
MIN_INDUSTRY = 4                  # too few descriptions to judge -> keep the cohort
MIN_RELATED = 3

# Words carried by every filing-derived summary. Killing them up front stops a
# two-document corpus from scoring "incorporated in Delaware" as a similarity.
_STOP = set("""a an the and or of for for to in on with by from as at is are was were be been being
its it this that these those company companies inc corp corporation ltd limited plc group holdings
holding also other others various including include includes provides provide providing offers offer
offering operates operate operating serves serve serving segment segments products product services
service solutions business businesses customers customer clients client markets market well through
under name formerly changed headquartered founded incorporated known which their there addition based
united states approximately additionally furthermore subsidiary subsidiaries operations related
primarily located consists consist""".split())


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", (text or "").lower())
            if len(w) > 2 and w not in _STOP]


def tfidf(texts: dict[str, str]) -> dict[str, dict[str, float]]:
    """L2-normalised log-TF x smoothed-IDF vectors, one per symbol."""
    counts: dict[str, dict[str, int]] = {}
    df: dict[str, int] = {}
    for sym, text in texts.items():
        c: dict[str, int] = {}
        for w in _tokens(text):
            c[w] = c.get(w, 0) + 1
        counts[sym] = c
        for w in c:
            df[w] = df.get(w, 0) + 1
    n = len(texts)
    out: dict[str, dict[str, float]] = {}
    for sym, c in counts.items():
        v = {w: (1 + math.log(k)) * (math.log((n + 1) / (df[w] + 1)) + 1.0)
             for w, k in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values()))
        out[sym] = {w: x / norm for w, x in v.items()} if norm else {}
    return out


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def _median(xs: list[float]) -> float | None:
    s = sorted(xs)
    return s[len(s) // 2] if s else None


def verdict(subject: str, texts: dict[str, str], industry: list[str],
            related: list[str]) -> dict[str, Any]:
    """Pure: should the industry cohort be rejected? No network, no vendor."""
    have = {s for s, t in texts.items() if _tokens(t)}
    ind = [s for s in industry if s in have and s != subject]
    rel = [s for s in related if s in have and s != subject and s not in ind]
    out: dict[str, Any] = {"industry_fit": None, "related_fit": None,
                           "reject": False, "reason": None}
    if subject not in have:
        out["reason"] = "no business description for this company"
        return out
    if len(ind) < MIN_INDUSTRY:
        out["reason"] = "too few descriptions to judge"
        return out
    vecs = tfidf({s: texts[s] for s in [subject] + ind + rel})
    sim = {s: cosine(vecs[subject], vecs[s]) for s in ind + rel}
    im = _median([sim[s] for s in ind])
    out["industry_fit"] = round(im, 3)
    out["similarity"] = {s: round(v, 3) for s, v in sim.items()}
    if len(rel) < MIN_RELATED:
        # JPM's co-viewed set is Citi, BofA and Wells Fargo — already in the
        # cohort. Nothing independent is left to test it against, and the
        # overlap is itself evidence the classification is right.
        out["reason"] = ("co-viewed companies are already in the cohort"
                         if set(industry) & set(related) else "too few descriptions to judge")
        return out
    rm = _median([sim[s] for s in rel])
    out["related_fit"] = round(rm, 3)
    if im < FIT_FLOOR and rm > 0 and im < FIT_RATIO * rm:
        out["reject"] = True
        out["reason"] = "industry cohort is in a different business"
    return out


def rank_replacements(subject: str, texts: dict[str, str], candidates: list[str],
                      citations: dict[str, int] | None = None,
                      strong_cites: int = MIN_CITATIONS) -> list[tuple[int, str]]:
    """Candidates that genuinely resemble the subject, as (tier, symbol).

    Two signals, because neither is reliable alone at the tail:

      * description similarity — a relative floor, not an absolute one, since
        it runs at very different levels across industries (bank summaries
        score ~0.3 against each other, semiconductor summaries ~0.14), so a
        fixed cut would empty one sector and admit anything in another;
      * co-citation — how many of the subject's neighbours also name the
        symbol. Nebius writes about an AI cloud platform and IREN about
        data-centre construction, so their descriptions score only 0.046
        against each other, below the floor — yet four of IREN's five
        neighbours name Nebius. Vocabulary misses peers the graph catches.

    A candidate is in if it clears the similarity floor OR is named by
    `strong_cites` neighbours (a majority of them). Tier 0 needs one of the two
    signals to be strong, tier 1 is the rest, kept only to fill a short table.
    For IREN that admits Nebius on two citations to spare and still leaves out
    a drone maker (ONDS, similarity 0.048, one citation) and a nuclear
    developer (OKLO, two citations, similarity 0.016) that the graph co-views
    for theme rather than business.
    """
    cites = citations or {}
    pool = [s for s in dict.fromkeys(candidates) if s != subject and _tokens(texts.get(s, ""))]
    if not pool or not _tokens(texts.get(subject, "")):
        return []
    vecs = tfidf({s: texts[s] for s in [subject] + pool})
    sim = sorted(((cosine(vecs[subject], vecs[s]), s) for s in pool), reverse=True)
    best = sim[0][0]
    cut = max(KEEP_FRACTION * best, KEEP_FLOOR)
    out = []
    for v, s in sim:
        many = cites.get(s, 0) >= strong_cites
        if v < cut and not many:
            continue
        out.append((0 if (v >= STRONG_FRACTION * best or many) else 1, s))
    return sorted(out, key=lambda t: t[0])


# ── vendor access ───────────────────────────────────────────────────────────
def _cache():
    from .peers import _cache_get, _cache_put
    return _cache_get, _cache_put


def related_symbols(symbol: str) -> list[str]:
    """Yahoo's co-viewed set. Behavioural, so it survives a wrong taxonomy."""
    get, put = _cache()
    key = f"related_{symbol}"
    hit = get(key, _TTL_RELATED)
    if hit is not None:
        return hit
    out: list[str] = []
    try:
        url = "https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/" + symbol
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            res = (json.load(r).get("finance") or {}).get("result") or []
        if res:
            out = [str(x.get("symbol", "")).upper() for x in (res[0].get("recommendedSymbols") or [])]
            out = [s for s in out if s and s != symbol.upper()]
    except Exception:                              # noqa: BLE001 — absence is handled
        return []                                  # not cached: a transient 404 must not stick
    put(key, out)
    return out


def profile(symbol: str) -> dict[str, Any]:
    """Business description and size. Cached for a week — this is static text."""
    get, put = _cache()
    key = f"profile_{symbol}"
    hit = get(key, _TTL_PROFILE)
    if hit is not None:
        return hit
    import yfinance as yf
    p: dict[str, Any] = {"symbol": symbol, "summary": "", "market_cap": None, "industry_key": None}
    try:
        info = yf.Ticker(symbol).info or {}
        p.update({"summary": info.get("longBusinessSummary") or "",
                  "market_cap": info.get("marketCap"),
                  "industry_key": info.get("industryKey"),
                  "name": info.get("shortName") or info.get("longName")})
    except Exception:                              # noqa: BLE001
        return p                                   # not cached: retry next time
    put(key, p)
    return p


def profiles(symbols: list[str]) -> dict[str, dict[str, Any]]:
    syms = list(dict.fromkeys(s for s in symbols if s))
    if not syms:
        return {}
    with ThreadPoolExecutor(min(10, len(syms))) as ex:
        return {p["symbol"]: p for p in ex.map(profile, syms)}


def _domestic(symbols: list[str], subject: str) -> list[str]:
    # "." marks a foreign-exchange listing (600519.SS); the US card skips those,
    # as _us_candidates already does for the industry cohort.
    return [s for s in symbols if s and "." not in s and s != subject]


def repair(subject: str, industry_picked: list[str], n: int,
           known: dict[str, str] | None = None,
           caps: dict[str, float] | None = None) -> dict[str, Any]:
    """Judge the industry cohort and, if it fails, build a replacement.

    `known` carries descriptions the caller already has — the peer card fetches
    the cohort's anyway for its metrics, so passing them in leaves only the
    co-viewed names to fetch, which is the difference between a page that waits
    on thirteen vendor round-trips and one that waits on five.

    Returns {reject, verdict, ranked}; `ranked` is [(symbol, market_cap)]
    largest first, including the subject, for the caller's own selection.
    """
    related = _domestic(related_symbols(subject), subject)
    if not related:
        return {"reject": False, "verdict": {"reason": "no co-viewed set available"}}
    texts = dict(known or {})
    caps = dict(caps or {})
    missing = [s for s in [subject] + industry_picked + related if s not in texts]
    probe = profiles(missing)
    texts.update({s: p.get("summary", "") for s, p in probe.items()})
    caps.update({s: float(p.get("market_cap") or 0.0) for s, p in probe.items()})
    v = verdict(subject, texts, industry_picked, related)
    if not v.get("reject"):
        return {"reject": False, "verdict": v}

    # One hop out through the co-viewed graph: the subject's neighbours and
    # theirs. Five symbols is too thin a peer table on its own.
    pool = list(related)
    cites: dict[str, int] = {s: 1 for s in related}
    with ThreadPoolExecutor(min(6, len(related))) as ex:
        for extra in ex.map(related_symbols, related):
            for s in _domestic(extra, subject):
                pool.append(s)
                cites[s] = cites.get(s, 0) + 1
    pool = list(dict.fromkeys(pool))
    fetched = profiles([s for s in pool if s not in texts])
    texts.update({s: p.get("summary", "") for s, p in fetched.items()})
    caps.update({s: float(p.get("market_cap") or 0.0) for s, p in fetched.items()})
    # "Named by a majority of the neighbours" — with five neighbours that is
    # three, which admits Nebius for IREN and excludes the two-citation names.
    strong = max(MIN_CITATIONS, (len(related) + 1) // 2)
    keep = rank_replacements(subject, texts, pool, cites, strong)[: max(n * 3, 12)]

    # Size order within each tier, so the table reads like every other peer
    # table while the weaker matches stay behind the stronger ones.
    def cap(sym: str) -> float:
        return float(caps.get(sym) or 0.0)
    ranked: list[tuple[str, float]] = []
    for tier in (0, 1):
        ranked += sorted(((s, cap(s)) for t, s in keep if t == tier), key=lambda x: -x[1])
    ranked.insert(_subject_slot(ranked, cap(subject)), (subject, cap(subject)))
    return {"reject": True, "verdict": v, "ranked": ranked,
            "pool_size": len([1 for t, _ in keep if t == 0])}


def _subject_slot(ranked: list[tuple[str, float]], cap: float) -> int:
    """Where the subject sits by size among the tier-0 names — _select reads the
    subject's rank to decide between 'largest' and 'largest + nearest in size'."""
    for i, (_, c) in enumerate(ranked):
        if c < cap:
            return i
    return len(ranked)
