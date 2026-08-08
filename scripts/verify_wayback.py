#!/usr/bin/env python3
"""Quality-check data/wayback -> data/wayback/quality.json

Asked directly whether this data could be fake. It is not — mention peaks land on
documented events, with the right tickers, dates and magnitudes:

    CLOV  2021-06-07    266 mentions, close $11.92
          2021-06-08  4,284 mentions, close $22.15    the Clover Health squeeze, +86%
    AMC   2021-06-08  1,890 mentions, $485            the June 2021 AMC peak
    GME   2022-03-23  2,371 mentions, 33.5% 5d range  the RC Ventures disclosure week

Nothing garbled reproduces four independent, checkable events on the right days.

But some of it *is* unusable, and that is what this records. ApeWisdom occasionally
served a frozen page: five consecutive days in September 2025 are byte-identical
despite five separate crawls at five different hours, and 2023-03-21 matches
2023-05-17 to 98.4% two months apart. Those days are one reading wearing several
dates. Against a baseline of 3.6% median similarity between adjacent days, they are
not subtle — they were simply invisible until something looked.

Flagged, not deleted. The captures are what archive.org holds and rewriting them
would lose the evidence; an analysis reads quality.json and skips what it says to.

    python3 scripts/verify_wayback.py           # write quality.json, report
    python3 scripts/verify_wayback.py --check   # exit 1 if the file is out of date
"""
import hashlib, json, os, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WB = os.path.join(ROOT, "data", "wayback")
OUT = os.path.join(WB, "quality.json")
SIMILAR = 60.0      # % of shared tickers with an identical count; the median is 3.6
MIN_SHARED = 20     # below this a comparison is noise


def load():
    days = sorted(n[:-5] for n in os.listdir(WB)
                  if n.endswith(".json") and n[:-5].count("-") == 2)
    out = {}
    for d in days:
        with open(os.path.join(WB, f"{d}.json")) as f:
            out[d] = json.load(f)
    return days, out


def compute(days, snaps):
    counts = {d: {r["ticker"]: r["mentions"] for r in s["filters"]["wallstreetbets"]}
              for d, s in snaps.items()}
    suspect = {}

    # Identical content under different dates. A crawl an hour apart legitimately
    # repeats; a crawl a day apart does not.
    groups = {}
    for d, s in snaps.items():
        key = hashlib.sha1(json.dumps(s["filters"], sort_keys=True).encode()).hexdigest()
        groups.setdefault(key, []).append(d)
    for key, members in groups.items():
        if len(members) > 1:
            for d in members:
                suspect[d] = f"byte-identical to {', '.join(x for x in sorted(members) if x != d)}"

    # Near-identical to the previous available day. Catches a page that froze without
    # freezing perfectly.
    for a, b in zip(days, days[1:]):
        A, B = counts[a], counts[b]
        shared = [t for t in A if t in B]
        if len(shared) < MIN_SHARED:
            continue
        same = sum(1 for t in shared if A[t] == B[t]) / len(shared) * 100
        if same > SIMILAR and b not in suspect:
            suspect[b] = f"{same:.1f}% of {len(shared)} shared tickers identical to {a}"

    # Column alignment. Ranks ascend and mentions descend, or the parse slipped.
    for d, s in snaps.items():
        rows = s["filters"]["wallstreetbets"]
        ranks = [r["rank"] for r in rows]
        ms = [r["mentions"] for r in rows]
        if ranks != sorted(ranks) or ms != sorted(ms, reverse=True):
            suspect[d] = "rank/mentions not monotonic — parse misaligned"

    sims = []
    for a, b in zip(days, days[1:]):
        A, B = counts[a], counts[b]
        shared = [t for t in A if t in B]
        if len(shared) >= MIN_SHARED:
            sims.append(sum(1 for t in shared if A[t] == B[t]) / len(shared) * 100)

    return {
        "days": len(days),
        "span": [days[0], days[-1]] if days else [],
        # The list itself, so the site can offer these dates in its picker without
        # fetching 230 files to find out which exist. They were browsable nowhere:
        # the data was in the repo and feeding the cross-period study, but the date
        # dropdown still started at 2026-05-01, which is not what "put it in" meant.
        "days_list": days,
        "usable": len(days) - len(suspect),
        "median_adjacent_similarity_pct": round(st.median(sims), 1) if sims else None,
        "similarity_threshold_pct": SIMILAR,
        "suspect": dict(sorted(suspect.items())),
    }


def main():
    days, snaps = load()
    q = compute(days, snaps)

    if "--check" in sys.argv:
        try:
            with open(OUT) as f:
                shipped = json.load(f)
        except Exception:
            print("quality.json missing", file=sys.stderr)
            sys.exit(1)
        if shipped != q:
            print("quality.json disagrees with a fresh recomputation", file=sys.stderr)
            sys.exit(1)
        print(f"quality.json matches · {q['usable']}/{q['days']} usable")
        return

    with open(OUT, "w") as f:
        json.dump(q, f, indent=1)
        f.write("\n")
    print(f"{q['days']} days {q['span'][0]} → {q['span'][1]}")
    print(f"  usable            {q['usable']}")
    print(f"  suspect           {len(q['suspect'])}")
    print(f"  median adjacent similarity {q['median_adjacent_similarity_pct']}% "
          f"(flagging above {SIMILAR}%)")
    for d, why in q["suspect"].items():
        print(f"    {d}  {why}")


if __name__ == "__main__":
    main()
