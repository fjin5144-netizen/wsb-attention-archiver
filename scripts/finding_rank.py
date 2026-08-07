#!/usr/bin/env python3
"""The same question over five years -> data/finding_rank.json

The site's claim rests on 99 days. The archive cannot be split across periods, and
that split is the test a v2 result failed the moment its window was long enough to
halve. The Wayback backfill reaches 2021 but is far too sparse for the spike
definition — `>= 3x the median of the prior 20 archive days` needs those 20 days to be
20 days, and there they span two months.

So this asks a question that needs no lookback at all: **a ticker holding a top-3 spot
on the board that day**. That is decidable from one snapshot, which makes it immune to
sparsity — and it is the same definition applied to both datasets, by the same code,
so the comparison is real rather than rhymed.

It is a different question from the site's headline, not a second opinion on it. "The
most-discussed name today" and "a name whose mentions tripled" overlap without being
the same claim. Read the two side by side, not as confirmation.

House rules carry over unchanged: median beside trimmed mean beside mean, and the
benchmark is the same tickers on days they were *not* top-3, drawn by hash so the
artefact reproduces. Never zero — this basket beats SPY unaided.

Suspect Wayback days are excluded via quality.json: eleven of them are a frozen page
wearing several dates.

    python3 scripts/finding_rank.py
    python3 scripts/finding_rank.py --check
"""
import hashlib, json, os, re, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "finding_rank.json")
HORIZONS = (5, 10, 20)
TOP_N = 3
TRIM = 0.10


def load_days(folder, skip=()):
    out = {}
    for n in sorted(os.listdir(folder)):
        if not re.fullmatch(r"\d{4}-\d\d-\d\d\.json", n) or n[:-5] in skip:
            continue
        with open(os.path.join(folder, n)) as f:
            rows = (json.load(f).get("filters") or {}).get("wallstreetbets") or []
        if rows:
            out[n[:-5]] = [r["ticker"] for r in rows]
    return out


def forward(prices, tk, day, n):
    """Return over n sessions from the first session at or after `day`."""
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


def trimmed_mean(v, pct=TRIM):
    v = sorted(v)
    k = int(len(v) * pct)
    return st.mean(v[k:len(v) - k]) if len(v) - 2 * k > 0 else st.mean(v)


def describe(v):
    return {"n": len(v), "median": round(st.median(v), 2),
            "trimmed_mean": round(trimmed_mean(v), 2), "mean": round(st.mean(v), 2),
            "win_rate": round(sum(1 for x in v if x > 0) / len(v) * 100)}


def analyse(label, boards, prices):
    """Top-3 days against days the same ticker was on the board and not top-3."""
    days = sorted(boards)
    top_days, seen_days = {}, {}
    for d in days:
        for rank, tk in enumerate(boards[d]):
            seen_days.setdefault(tk, []).append(d)
            if rank < TOP_N:
                top_days.setdefault(tk, []).append(d)

    rows = []
    for n in HORIZONS:
        hot, plac = [], []
        for tk, ds in top_days.items():
            pool = [d for d in seen_days[tk] if d not in set(ds)]
            for d in ds:
                r = forward(prices, tk, d, n)
                if r is not None:
                    hot.append(r)
                if not pool:
                    continue
                h = int(hashlib.sha1(f"{label}|{tk}|{d}|{n}".encode()).hexdigest(), 16)
                r = forward(prices, tk, pool[h % len(pool)], n)
                if r is not None:
                    plac.append(r)
        if not hot or not plac:
            continue
        row = {"sessions": n, "top3": describe(hot), "rest_of_board": describe(plac)}
        row["gap_median"] = round(row["top3"]["median"] - row["rest_of_board"]["median"], 2)
        rows.append(row)

    return {"label": label, "days": len(days), "span": [days[0], days[-1]],
            "tickers_ever_top3": len(top_days), "horizons": rows}


def by_era(boards, prices):
    """Split by year. The whole reason for reaching back: does it hold in 2021 and 2025,
    or only in the quarter the site happens to cover?"""
    out = []
    years = sorted({d[:4] for d in boards})
    for y in years:
        sub = {d: v for d, v in boards.items() if d[:4] == y}
        if len(sub) < 12:
            continue
        a = analyse(y, sub, prices)
        h = next((r for r in a["horizons"] if r["sessions"] == 10), None)
        if h and h["top3"]["n"] >= 10:
            out.append({"era": y, "days": a["days"], "n": h["top3"]["n"],
                        "median": h["top3"]["median"],
                        "rest": h["rest_of_board"]["median"], "gap": h["gap_median"]})
    return out


def compute():
    with open(os.path.join(ROOT, "data", "prices_hist.json")) as f:
        prices = json.load(f)

    wb = os.path.join(ROOT, "data", "wayback")
    skip = set()
    try:
        with open(os.path.join(wb, "quality.json")) as f:
            skip = set(json.load(f)["suspect"])
    except Exception:
        pass

    sparse = load_days(wb, skip) if os.path.isdir(wb) else {}
    daily = load_days(os.path.join(ROOT, "data", "apewisdom"))

    out = {"definition": f"a ticker holding a top-{TOP_N} spot on the board that day",
           "benchmark": "the same tickers on board days they were not top-3",
           "top_n": TOP_N, "excluded_suspect_days": len(skip),
           "datasets": []}
    if sparse:
        out["datasets"].append(analyse("wayback (sparse, 2021-2026)", sparse, prices))
        out["by_year"] = by_era(sparse, prices)
    if daily:
        out["datasets"].append(analyse("daily archive (2026)", daily, prices))
    return out


def main():
    q = compute()
    if "--check" in sys.argv:
        with open(OUT) as f:
            shipped = json.load(f)
        if shipped != q:
            print("finding_rank.json disagrees with a fresh recomputation", file=sys.stderr)
            sys.exit(1)
        print("finding_rank.json matches")
        return

    with open(OUT, "w") as f:
        json.dump(q, f, indent=1)
        f.write("\n")

    for ds in q["datasets"]:
        print(f"\n{ds['label']} · {ds['days']} days {ds['span'][0]} → {ds['span'][1]} "
              f"· {ds['tickers_ever_top3']} tickers ever top-{TOP_N}")
        for h in ds["horizons"]:
            t, r = h["top3"], h["rest_of_board"]
            print(f"  {h['sessions']:2d}d  top3 median {t['median']:+7.2f}% (n={t['n']:4d}, win {t['win_rate']:2d}%)"
                  f"   rest {r['median']:+7.2f}% (n={r['n']:4d})   gap {h['gap_median']:+6.2f}pp")
    if q.get("by_year"):
        print("\nby year (10 sessions):")
        for e in q["by_year"]:
            print(f"  {e['era']}  {e['days']:3d} days  n={e['n']:4d}  "
                  f"top3 {e['median']:+7.2f}%  rest {e['rest']:+7.2f}%  gap {e['gap']:+6.2f}pp")


if __name__ == "__main__":
    main()
