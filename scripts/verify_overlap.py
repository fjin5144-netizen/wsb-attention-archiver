#!/usr/bin/env python3
"""Hold the Reddit-derived counts against ApeWisdom's, every day both exist.

One day's comparison says the two disagree. It cannot say whether they disagree
*consistently*, and that is the whole question: a second measurement is only useful if
its relationship to the first is stable. A count that runs 20% high every day is a
different instrument reading the same thing. A count that runs high on Monday and low on
Tuesday is noise, and nothing downstream of it means anything.

So this reports the shape of the disagreement across the overlap, not its size on one
day:

  * the ratio of counts, per day, per ticker — its centre and its spread
  * rank correlation, because the site's claims are about which tickers were loud, not
    about the absolute numbers
  * the tickers that disagree in a way the rest do not, which is where the tokenisers
    part company

It deliberately does not fit a correction factor. Making one series look like the other
would destroy the only thing the second one is for.

    python3 scripts/verify_overlap.py                # every day with both
    python3 scripts/verify_overlap.py --top 25       # deeper into each board
    python3 scripts/verify_overlap.py --csv out.csv  # per-day, per-ticker pairs
"""
import csv, datetime as dt, gzip, json, os, re, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REDDIT = os.path.join(ROOT, "data", "reddit")
ARCHIVE = os.path.join(ROOT, "data", "apewisdom")

sys.path.insert(0, os.path.join(ROOT, "scripts"))
from compare_counts import WORDLIKE, counter  # noqa: E402  one definition, not two


def window_texts(lo, hi, cache={}):
    """Posts and comments in [lo, hi). Day files are cached — consecutive windows share
    most of their days, and re-reading a 26,000-line gzip per day is the whole runtime."""
    out = []
    day = dt.datetime.fromtimestamp(lo, dt.timezone.utc).date()
    last = dt.datetime.fromtimestamp(hi, dt.timezone.utc).date()
    while day <= last:
        key = day.isoformat()
        if key not in cache:
            rows = []
            for kind in ("comments", "posts"):
                p = os.path.join(REDDIT, key[:7], f"{key}-{kind}.jsonl.gz")
                if os.path.exists(p):
                    with gzip.open(p, "rt", encoding="utf-8") as f:
                        rows += [json.loads(l) for l in f]
            cache[key] = rows
            # Two days is all a 24h window can span; anything older is dead weight.
            for k in [k for k in cache if k < (day - dt.timedelta(days=2)).isoformat()]:
                del cache[k]
        out += [r for r in cache[key] if lo <= r["t"] < hi]
        day += dt.timedelta(days=1)
    return out


def covered(lo, hi):
    """Both days of the window present, or the count would be short for a reason that
    has nothing to do with either source."""
    a = dt.datetime.fromtimestamp(lo, dt.timezone.utc).date().isoformat()
    b = dt.datetime.fromtimestamp(hi, dt.timezone.utc).date().isoformat()
    for d in {a, b}:
        for kind in ("comments", "posts"):
            if not os.path.exists(os.path.join(REDDIT, d[:7], f"{d}-{kind}.jsonl.gz")):
                return False
    return True


def spearman(a, b):
    """Rank correlation, computed here rather than imported — the ranks are the claim."""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: -v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):                      # average ties, or equal counts skew it
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def main():
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 20
    csv_path = sys.argv[sys.argv.index("--csv") + 1] if "--csv" in sys.argv else None

    # --wayback runs the same comparison against the archive.org captures instead of the
    # daily collection. Same file shape, so it is a directory swap rather than a second
    # implementation — the point of the 2021-2025 fetch is to ask whether the 1.09 offset
    # is a fact about this instrument or a fact about the last three months, and a
    # separately-written checker could answer differently for reasons of its own.
    #
    # Two differences that matter and are handled here: captures are top 100 rather than
    # top 500, which only bites above --top 100; and eleven capture dates are flagged
    # frozen in quality.json, meaning ApeWisdom served archive.org one cached page under
    # several dates. Those are dropped — comparing a day's text against another day's
    # board would manufacture disagreement that says nothing about either source.
    archive = ARCHIVE
    skip = set()
    if "--wayback" in sys.argv:
        archive = os.path.join(ROOT, "data", "wayback")
        qp = os.path.join(archive, "quality.json")
        if os.path.exists(qp):
            with open(qp) as f:
                skip = set((json.load(f).get("suspect") or {}).keys())

    days = sorted(n[:-5] for n in os.listdir(archive)
                  if re.fullmatch(r"\d{4}-\d\d-\d\d\.json", n) and n[:-5] not in skip)
    pairs, per_day, rows = [], [], []
    for day in days:
        with open(os.path.join(archive, f"{day}.json")) as f:
            snap = json.load(f)
        if not snap.get("fetched_at"):
            continue
        # Two shapes in the archive: "...+00:00" from later runs and "...Z" from earlier
        # ones, and fromisoformat below 3.11 rejects the Z. Both are UTC.
        stamp = snap["fetched_at"].replace("Z", "+00:00")
        hi = int(dt.datetime.fromisoformat(stamp).timestamp())
        lo = hi - 86400
        if not covered(lo, hi):
            continue
        texts = [r["b"] for r in window_texts(lo, hi)]
        if not texts:
            continue
        board = sorted((snap.get("filters") or {}).get("wallstreetbets") or [],
                       key=lambda r: -r["mentions"])[:top]
        mine, theirs = [], []
        for r in board:
            hit = counter(r["ticker"])
            n = sum(1 for t in texts if hit(t))
            mine.append(n)
            theirs.append(r["mentions"])
            if r["mentions"] > 0:
                pairs.append((r["ticker"], n / r["mentions"]))
            rows.append([day, r["ticker"], n, r["mentions"]])
        rho = spearman(mine, theirs)
        ratios = [m / t for m, t in zip(mine, theirs) if t > 0]
        per_day.append((day, len(texts), st.median(ratios) if ratios else None, rho))
        print(f"{day}  {len(texts):>6} items · median ratio "
              f"{st.median(ratios):.2f} · rank correlation "
              f"{rho:.3f}" if rho is not None else f"{day}  (too few)", flush=True)

    if not per_day:
        print("no day has both an archived board and the Reddit text for its window")
        return

    meds = [m for _, _, m, _ in per_day if m]
    rhos = [r for _, _, _, r in per_day if r is not None]
    print(f"\n{len(per_day)} overlapping days")
    print(f"  ratio of counts   median {st.median(meds):.2f}"
          + (f" · spread {min(meds):.2f}–{max(meds):.2f}" if len(meds) > 1 else "")
          + (f" · day-to-day sd {st.stdev(meds):.3f}" if len(meds) > 2 else ""))
    print(f"  rank correlation  median {st.median(rhos):.3f}"
          + (f" · worst {min(rhos):.3f}" if len(rhos) > 1 else ""))

    # Where the two tokenisers actually part company.
    by_tk = {}
    for tk, r in pairs:
        by_tk.setdefault(tk, []).append(r)
    odd = sorted(((tk, st.median(v), len(v)) for tk, v in by_tk.items() if len(v) >= 3),
                 key=lambda x: -abs(x[1] - st.median(meds)))[:8]
    print("\n  furthest from the common ratio:")
    for tk, r, n in odd:
        print(f"    {tk:<7} {r:>5.2f}  over {n} days"
              + ("   (word-like: $ only)" if tk in WORDLIKE else ""))

    if csv_path:
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["day", "ticker", "reddit_count", "apewisdom_mentions"])
            w.writerows(rows)
        print(f"\n{len(rows)} pairs -> {csv_path}")


if __name__ == "__main__":
    main()
