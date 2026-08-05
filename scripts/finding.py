#!/usr/bin/env python3
"""Compute the site's own answer -> data/finding.json.

The site measured 246 spikes and never said what they added up to. Its closest
thing to a conclusion read "bullish +0.1%" directly above "49% closed down", on a
horizon nobody chose. A reader could not tell what the project had found, which is
the one thing only this project can tell them.

House rules, from the research and not negotiable here:

  * median and trimmed mean side by side — the distribution is violently
    right-skewed and a mean reports its tail
  * the benchmark is a matched placebo, never zero. This basket beats SPY on its
    own, so "negative versus zero" carries almost no information; "negative versus
    the same tickers on a random day" does
  * every number is provisional. The archive is one quarter long and cannot be
    split across periods, and a v2 result with p=0.000 flipped sign when it finally
    was. Nothing here is a finding; it is a thing to look at again in six months

Deterministic: the placebo day is drawn by hashing (ticker, date, horizon), so a
rerun reproduces the file exactly and `--check` means something.

    python3 scripts/finding.py           # write data/finding.json
    python3 scripts/finding.py --check   # recompute, exit 1 on drift
"""
import hashlib, json, os, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HORIZONS = (5, 10, 20)
HEADLINE = 20
TRIM = 0.10


def load():
    with open(os.path.join(ROOT, "data", "events.json")) as f:
        events = json.load(f)
    with open(os.path.join(ROOT, "data", "prices.json")) as f:
        prices = json.load(f)
    with open(os.path.join(ROOT, "data", "days.json")) as f:
        days = json.load(f)
    return events, prices, days


def forward(prices, tk, day, n):
    """Return over n sessions from the first session at or after `day`.

    Sessions, not calendar days: a spike can land on a weekend or a holiday, and
    the whole point of the number is what the market did next.
    """
    p = prices.get(tk)
    if not p:
        return None
    d = p["d"]
    if day in d:
        i = d.index(day)
    else:
        i = next((k for k, x in enumerate(d) if x > day), -1)
    if i < 0 or i + n >= len(p["c"]) or not p["c"][i]:
        return None
    return (p["c"][i + n] / p["c"][i] - 1) * 100


def trimmed_mean(vals, pct=TRIM):
    v = sorted(vals)
    k = int(len(v) * pct)
    return st.mean(v[k:len(v) - k]) if len(v) - 2 * k > 0 else st.mean(v)


def describe(vals):
    return {
        "n": len(vals),
        "median": round(st.median(vals), 2),
        "trimmed_mean": round(trimmed_mean(vals), 2),
        "mean": round(st.mean(vals), 2),
        "win_rate": round(sum(1 for x in vals if x > 0) / len(vals) * 100),
    }


def placebo_day(tk, day, n, candidates):
    """A non-event day for the same ticker, chosen by hash rather than by chance.

    Randomness would make the artefact irreproducible, which would make the golden
    test that recomputes it meaningless.
    """
    h = int(hashlib.sha256(f"{tk}|{day}|{n}".encode()).hexdigest(), 16)
    return candidates[h % len(candidates)]


def compute(events, prices, days):
    event_days = {}
    for e in events:
        event_days.setdefault(e["tk"], set()).add(e["d"])

    horizons = []
    for n in HORIZONS:
        real, plac = [], []
        for e in events:
            r = forward(prices, e["tk"], e["d"], n)
            if r is not None:
                real.append(r)
            pool = [d for d in days if d not in event_days[e["tk"]]]
            if not pool:
                continue
            r = forward(prices, e["tk"], placebo_day(e["tk"], e["d"], n, pool), n)
            if r is not None:
                plac.append(r)
        if not real or not plac:
            continue
        row = {"sessions": n, "spike": describe(real), "placebo": describe(plac)}
        row["gap_median"] = round(row["spike"]["median"] - row["placebo"]["median"], 2)
        horizons.append(row)

    ends = [v["d"][-1] for v in prices.values() if v and v.get("d")]
    return {
        "events": len(events),
        "archive_days": len(days),
        "span": [days[0], days[-1]] if days else [],
        "prices_through": max(ends) if ends else None,
        "headline_sessions": HEADLINE,
        "horizons": horizons,
    }


def main():
    events, prices, days = load()
    out = compute(events, prices, days)
    path = os.path.join(ROOT, "data", "finding.json")

    if "--check" in sys.argv:
        with open(path) as f:
            shipped = json.load(f)
        if shipped != out:
            print("finding.json disagrees with a fresh recomputation", file=sys.stderr)
            sys.exit(1)
        print("finding.json matches the archive")
        return

    with open(path, "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    head = next(h for h in out["horizons"] if h["sessions"] == HEADLINE)
    print(f"{out['events']} events over {out['archive_days']} archive days "
          f"({out['span'][0]} .. {out['span'][1]}) -> data/finding.json")
    print(f"  {HEADLINE}-session: spike median {head['spike']['median']:+.2f}% "
          f"(n={head['spike']['n']}, win {head['spike']['win_rate']}%) vs "
          f"placebo {head['placebo']['median']:+.2f}% -> gap {head['gap_median']:+.2f}pp")


if __name__ == "__main__":
    main()
