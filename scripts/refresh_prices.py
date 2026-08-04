#!/usr/bin/env python3
"""Refresh the PRICES block embedded in index.html.

The attention archive grows every day through the workflow. The price pack did not:
it was pasted into index.html by hand, so it silently fell behind — 17 days by the
time anyone noticed, which is why event charts drew mention bars to the right edge
while the price line stopped short. This closes that gap and is meant to run on the
same schedule as the archiver, so it cannot reopen.

Universe is derived, not hard-coded: every ticker that has ever produced a confirmed
spike, plus the research basket. A newly-spiking name therefore gets prices without
anyone editing a list.

Failure policy is conservative. A ticker whose fetch fails keeps whatever history is
already embedded rather than being dropped — a stale series is worth more than a
missing one, and the page already reports how far behind prices are.

    python3 scripts/refresh_prices.py            # rewrite index.html in place
    python3 scripts/refresh_prices.py --dry-run  # report only
"""
import json, os, re, subprocess, sys, time, datetime as dt
import statistics as stats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")
ARCHIVE = os.path.join(ROOT, "data", "apewisdom")

HOT_FLOOR, HOT_X = 30, 3          # must match the thresholds in index.html
START = "2026-04-01"              # ~20 trading days of run-up before the archive opens
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def read_index():
    with open(INDEX) as f:
        s = f.read()
    i = s.find("const PRICES=")
    if i < 0:
        sys.exit("PRICES block not found in index.html")
    j = s.find("};", i) + 1
    return s, i, j, json.loads(s[i + len("const PRICES="):j])


def spike_universe():
    """Tickers that have ever crossed the confirmed tier, from the archive itself."""
    days, maps = [], {}
    for name in sorted(os.listdir(ARCHIVE)):
        if not name.endswith(".json"):
            continue
        d = name[:-5]
        with open(os.path.join(ARCHIVE, name)) as f:
            snap = json.load(f)
        rows = (snap.get("filters") or {}).get("wallstreetbets") or []
        if not rows:
            continue
        days.append(d)
        maps[d] = {r["ticker"]: r["mentions"] for r in rows}
    universe = set()
    for d in maps:
        universe |= set(maps[d])
    hot = set()
    for tk in universe:
        for i in range(20, len(days)):
            cur = maps[days[i]].get(tk, 0)
            if cur < HOT_FLOOR:
                continue
            med = stats.median([maps[days[j]].get(tk, 0) for j in range(i - 20, i)])
            if cur >= HOT_X * max(med, 1):
                hot.add(tk)
                break
    return hot, (days[-1] if days else "")


def fetch(tk):
    url = f"https://stockanalysis.com/api/symbol/s/{tk}/history?range=5Y&period=Daily"
    r = subprocess.run(["curl", "-s", "--max-time", "30", "-H", f"User-Agent: {UA}", url],
                       capture_output=True, text=True)
    try:
        rows = json.loads(r.stdout)["data"]
    except Exception:
        return None
    d, c = [], []
    for b in sorted(rows, key=lambda x: x["t"]):
        if b["t"] < START or b.get("c") is None:
            continue
        d.append(b["t"]); c.append(round(float(b["c"]), 2))
    return {"d": d, "c": c} if len(d) > 20 else None


def main():
    dry = "--dry-run" in sys.argv
    src, i, j, old = read_index()
    hot, last_archive = spike_universe()

    basket = set(re.findall(r"[A-Z]{1,5}", (re.search(r"const BASKET=new Set\(\[([^\]]*)\]",
                 src) or type("", (), {"group": lambda *_: ""})()).group(1) or ""))
    want = sorted((hot | basket | set(old)) - {""})
    print(f"universe: {len(hot)} ever-spiked + {len(basket)} basket + "
          f"{len(old)} already embedded -> {len(want)} tickers")
    print(f"archive through {last_archive}")

    if dry:
        ends = {}
        for t, v in old.items():
            ends[v["d"][-1]] = ends.get(v["d"][-1], 0) + 1
        print("current price cutoffs:", dict(sorted(ends.items())))
        return

    fresh, kept, failed = {}, [], []
    for n, tk in enumerate(want, 1):
        got = fetch(tk)
        if got:
            fresh[tk] = got
        elif tk in old:
            fresh[tk] = old[tk]; kept.append(tk)
        else:
            failed.append(tk)
        if n % 25 == 0:
            print(f"  {n}/{len(want)}", flush=True)
        time.sleep(0.6)

    ends = {}
    for v in fresh.values():
        ends[v["d"][-1]] = ends.get(v["d"][-1], 0) + 1
    newest = max(ends) if ends else "?"
    print(f"fetched {len(fresh) - len(kept)} fresh, kept {len(kept)} stale, "
          f"{len(failed)} unavailable{': ' + ', '.join(failed) if failed else ''}")
    print(f"price cutoffs now: {dict(sorted(ends.items()))}")

    blob = "const PRICES=" + json.dumps(fresh, separators=(",", ":")) + ";"
    with open(INDEX, "w") as f:
        f.write(src[:i] + blob + src[j + 1:])
    print(f"index.html updated · prices through {newest} · archive through {last_archive}")


if __name__ == "__main__":
    main()
