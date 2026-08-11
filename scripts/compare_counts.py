#!/usr/bin/env python3
"""Count mentions from the raw Reddit text and hold it against ApeWisdom's figure.

This is the only check this project has on the source it is built from. Everything else
takes ApeWisdom's word: the archive records what the tracker said, and there has been no
way to ask whether it is right, or even what "right" would mean.

With data/reddit/ there is. The comparison exists to answer one question — *how close is
a count we control to the count we depend on* — and the answer is "close, and never
equal", which is worth knowing precisely.

Measured on 2026-08-11's read (the 24 hours to 05:52:39Z, 27,211 posts and comments):

    WULF   6 vs 5      NVDA 163 vs 150     MU  205 vs 200
    SPY  511 vs 403    TSLA  31 vs  24     GME   2 vs   3
    AAPL  47 vs  40    HOOD  11 vs   6     OPEN  0 vs   4

Nothing matches exactly. The direction is consistent — this count runs high on almost
everything — and the reasons are structural rather than fixable: their tokeniser is not
this one, their ticker universe is Infinite Marketcap's, and their deduplication within a
thread is not documented. OPEN goes the other way and shows the rule in action: it is an
English word, so ApeWisdom only counts `$OPEN`, and so does this.

The conclusion the numbers support is the one to hold: a count built here is a **second
series**, usable for asking whether the shape of the finding survives an independent
measurement. It is not a repair for the 75-day hole in the first series, and merging them
would produce a line that is neither.

    python3 scripts/compare_counts.py 2026-08-11
    python3 scripts/compare_counts.py 2026-08-11 --top 40
"""
import datetime as dt, gzip, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REDDIT = os.path.join(ROOT, "data", "reddit")

# Tickers that are also ordinary words. ApeWisdom counts these only with a dollar sign —
# its methodology names CFO and YOLO — so this does too, or every board would be topped
# by prepositions. The list is deliberately short and visible rather than a cleverer rule:
# a heuristic that silently decides what is a word is the kind of thing that should not be
# invisible in a project about counting.
WORDLIKE = {"OPEN", "ALL", "ON", "IT", "ARE", "SO", "BE", "GO", "NOW", "ANY", "CEO",
            "YOLO", "DD", "FOR", "AI", "EV", "PLAY", "LOVE", "TELL", "HAS", "NEW",
            "CAN", "ONE", "TWO", "SEE", "OUT", "WELL", "GOOD", "BIG", "RUN", "HOLD",
            "MOON", "CASH", "FREE", "REAL", "SAFE", "BEST", "TRUE", "WISH", "HOPE",
            "PUMP", "CAR", "GAIN", "LOSS", "RIDE", "TOP", "LOW", "HIGH", "NEXT"}


def load_window(lo, hi):
    """Every post and comment created in [lo, hi), from whatever days are on disk."""
    days = {dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()
            for t in (lo, hi, (lo + hi) // 2)}
    out = []
    for day in sorted(days):
        for kind in ("comments", "posts"):
            p = os.path.join(REDDIT, day[:7], f"{day}-{kind}.jsonl.gz")
            if not os.path.exists(p):
                continue
            with gzip.open(p, "rt", encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    if lo <= r["t"] < hi:
                        out.append(r["b"])
    return out


def counter(ticker):
    """One item containing the ticker counts once, which is ApeWisdom's rule."""
    dollar = re.compile(r"\$" + re.escape(ticker) + r"(?![A-Za-z0-9])", re.I)
    if ticker in WORDLIKE:
        return lambda t: bool(dollar.search(t))
    bare = re.compile(r"(?<![A-Za-z0-9$])" + re.escape(ticker) + r"(?![A-Za-z0-9])")
    return lambda t: bool(bare.search(t) or dollar.search(t))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        sys.exit("usage: compare_counts.py YYYY-MM-DD [--top N]")
    day = args[0]
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 15

    snap_path = os.path.join(ROOT, "data", "apewisdom", f"{day}.json")
    if not os.path.exists(snap_path):
        sys.exit(f"no archived board for {day}")
    with open(snap_path) as f:
        snap = json.load(f)
    read = dt.datetime.fromisoformat(snap["fetched_at"])
    hi = int(read.timestamp())
    lo = hi - 86400            # the window ApeWisdom's figure covers

    texts = load_window(lo, hi)
    if not texts:
        sys.exit(f"no data/reddit coverage for {day}'s window — "
                 f"fetch {dt.datetime.fromtimestamp(lo, dt.timezone.utc).date()} "
                 f"and {dt.datetime.fromtimestamp(hi, dt.timezone.utc).date()} first")

    rows = (snap.get("filters") or {}).get("wallstreetbets") or []
    rows = sorted(rows, key=lambda r: -r["mentions"])[:top]

    print(f"{day} · window {dt.datetime.fromtimestamp(lo, dt.timezone.utc):%m-%d %H:%M} → "
          f"{read:%m-%d %H:%M} UTC · {len(texts)} posts and comments\n")
    print(f"{'ticker':<8}{'here':>7}{'apewisdom':>11}{'diff':>8}")
    exact = 0
    for r in rows:
        hits = sum(1 for t in texts if counter(r["ticker"])(t))
        if hits == r["mentions"]:
            exact += 1
        print(f"{r['ticker']:<8}{hits:>7}{r['mentions']:>11}{hits - r['mentions']:>+8}"
              + ("  (word-like: $ only)" if r["ticker"] in WORDLIKE else ""))
    print(f"\nexact matches: {exact} of {len(rows)} — "
          f"a second series, not the same one")


if __name__ == "__main__":
    main()
