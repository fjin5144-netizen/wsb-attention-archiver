#!/usr/bin/env python3
"""Mention counts for the 75 days ApeWisdom never covered -> data/gap_series.json

2026-02-15 to 2026-04-30 is a hole nothing can fill from the tracker's side. ApeWisdom
has no data for it, archive.org crawled nothing, and the daily collection did not exist
yet. The raw r/wallstreetbets text does exist for those days, and it has been sitting on
disk unused since it was fetched: 75 days of posts and comments that the site cannot show
because the site only knows how to read ApeWisdom snapshots.

So this counts them under this project's own rule and writes them as **their own series**.
Three things make that a different measurement from the archive, and none of them can be
fixed by trying harder:

  * The window. An archived figure covers the 24 hours to whenever the tracker was read,
    which drifts between 21:00 and 23:30 UTC. There is no read time here, so the window is
    the UTC calendar day. Cleaner, and not the same.
  * The rule. compare_counts.counter, the same one the cross-check uses — which runs about
    1.12x ApeWisdom over the 105 days where both exist, consistently enough to be an
    instrument offset rather than noise, but an offset all the same.
  * The universe. ApeWisdom resolves tickers against Infinite Marketcap's list and this
    resolves against whatever the archive has ever seen, so a name that first appeared in
    June is countable here in February while a name that died before May is not.

Which is why the file says `series: "own"` and the site draws it as a separate segment
with a visible break. Splicing it onto the archive would turn "the ruler changed" into
"attention changed", and that is the one reading this project exists to prevent.

    python3 scripts/gap_series.py
    python3 scripts/gap_series.py --floor 3     # keep tickers reaching this on any day
"""
import collections, datetime as dt, gzip, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REDDIT = os.path.join(ROOT, "data", "reddit")
ARCHIVE = os.path.join(ROOT, "data", "apewisdom")
OUT = os.path.join(ROOT, "data", "gap_series.json")

sys.path.insert(0, os.path.join(ROOT, "scripts"))
from compare_counts import WORDLIKE          # noqa: E402
from divergence import tokens, hits          # noqa: E402  one tokeniser, proven against the regex

GAP_FROM, GAP_TO = "2026-02-15", "2026-04-30"


def universe():
    """Every ticker the archive has ever carried.

    Not the current board: a name loud in March may have left by May, and using today's
    board would silently drop exactly the names the gap is interesting for.
    """
    seen = set()
    for n in os.listdir(ARCHIVE):
        if not re.fullmatch(r"\d{4}-\d\d-\d\d\.json", n):
            continue
        with open(os.path.join(ARCHIVE, n)) as f:
            for r in (json.load(f).get("filters") or {}).get("wallstreetbets") or []:
                seen.add(r["ticker"])
    return seen


def day_counts(day, cand):
    """One UTC calendar day, counted once per item — the archive's own rule."""
    rows = []
    for kind in ("comments", "posts"):
        p = os.path.join(REDDIT, day[:7], f"{day}-{kind}.jsonl.gz")
        if not os.path.exists(p):
            return None
        with gzip.open(p, "rt", encoding="utf-8") as f:
            rows += [json.loads(l) for l in f]
    lo = int(dt.datetime.strptime(day, "%Y-%m-%d")
             .replace(tzinfo=dt.timezone.utc).timestamp())
    c, dollars = collections.Counter(), collections.Counter()
    n = 0
    for r in rows:
        if not (lo <= r["t"] < lo + 86400):
            continue
        n += 1
        b, d = tokens(r["b"])
        # WORDLIKE names are $-only, so they cannot be read off the bare set. Everything
        # else is one set intersection per item, which is why 1,900 candidates costs the
        # same as 50 — the work is proportional to the item, not to the candidate list.
        for tk in b & cand:
            if tk not in WORDLIKE:
                c[tk] += 1
        for tk in d & cand:
            if tk in WORDLIKE or tk not in b:
                c[tk] += 1
            dollars[tk] += 1
    return c, dollars, n


def main():
    floor = int(sys.argv[sys.argv.index("--floor") + 1]) if "--floor" in sys.argv else 3
    cand = universe()
    d0, d1 = dt.date.fromisoformat(GAP_FROM), dt.date.fromisoformat(GAP_TO)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

    per, dol, items, missing = {}, collections.Counter(), {}, []
    for day in days:
        got = day_counts(day, cand)
        if got is None:
            missing.append(day)
            continue
        per[day], d, items[day] = got
        dol.update(d)
        print(f"  {day}  {items[day]:>6} items · {len(per[day])} tickers", flush=True)
    if missing:
        print(f"no text for {len(missing)} day(s): {missing[:5]}")
    have = [d for d in days if d in per]
    if not have:
        sys.exit("no raw text for the gap — run scripts/fetch_reddit.py first")

    peak = {tk: max(per[d].get(tk, 0) for d in have) for tk in cand}
    keep = sorted(tk for tk, v in peak.items() if v >= floor)
    counts = {tk: [per[d].get(tk, 0) for d in have] for tk in keep}

    with open(OUT, "w") as f:
        json.dump({
            "_note": ("This project's own count of r/wallstreetbets for the 75 days ApeWisdom "
                      "never covered. It is NOT the archive's series and must not be joined to "
                      "it: different window (UTC day, not the tracker's rolling 24h), different "
                      "rule (~1.12x ApeWisdom where both exist), different ticker universe. See "
                      "the docstring in scripts/gap_series.py."),
            "series": "own",
            "rule": "compare_counts.counter — one item containing the ticker counts once",
            "window": "UTC calendar day",
            "offset_vs_archive": "about 1.12x, measured over the 105 days where both exist",
            "days": have,
            "items_per_day": [items[d] for d in have],
            "floor": floor,
            "counts": counts,
            # There is no second source for these 75 days, so the one test that needs only
            # the text carries the whole weight: does anyone ever write $TICKER? Over the
            # window SPY sits at 1.6%, MSFT 2.9%, MU 12.6% — while A is 0 of 2,161, YOU 0 of
            # 521, PM 0 of 683 and P 0 of 554. Single letters on an all-caps board are the
            # worst case in the whole project, and with nothing to cross-check against they
            # would otherwise top this series unchallenged: A came second only to SPY.
            #
            # Shipped rather than filtered, because a row removed cannot be argued with. The
            # site marks them the same way it marks the archive's artefacts.
            "dollar": {tk: dol.get(tk, 0) for tk in keep},
        }, f, separators=(",", ":"))
    tot = sum(items[d] for d in have)
    print(f"\n{len(have)} days · {tot:,} posts and comments · {len(keep)} tickers "
          f"reaching {floor}+ on some day -> data/gap_series.json "
          f"({os.path.getsize(OUT)/1e6:.1f} MB)")
    top = sorted(keep, key=lambda tk: -sum(counts[tk]))[:14]
    print("  loudest over the window:")
    for tk in top:
        tot, dl = sum(counts[tk]), dol.get(tk, 0)
        pct = dl / tot * 100 if tot else 0
        print(f"    {tk:<6}{tot:>7}  ${tk} {dl:>4}  {pct:>5.2f}%"
              + ("   <- never written with a dollar sign" if tot >= 300 and dl == 0 else ""))


if __name__ == "__main__":
    main()
