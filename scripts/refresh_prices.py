#!/usr/bin/env python3
"""Refresh the historical prices -> data/prices.json.

Universe is derived, not hard-coded: every ticker that has ever produced a confirmed
spike, plus the research basket. A newly-spiking name therefore gets prices without
anyone editing a list.

Failure policy is conservative. A ticker whose fetch fails keeps whatever history is
already saved rather than being dropped.

Fetches primarily from stockanalysis, failing over to Yahoo Finance via cookie dance.

    python3 scripts/refresh_prices.py            # rewrite data/prices.json
    python3 scripts/refresh_prices.py --dry-run  # report only
"""
import json, os, re, subprocess, sys, time, datetime as dt
import statistics as stats
import urllib.request, urllib.parse, http.cookiejar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")
ARCHIVE = os.path.join(ROOT, "data", "apewisdom")
PRICES_FILE = os.path.join(ROOT, "data", "prices.json")

HOT_FLOOR, HOT_X = 30, 3          # must match the thresholds in index.html
START = "2026-04-01"              # ~20 trading days of run-up before the archive opens
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
Y1 = "https://query1.finance.yahoo.com"


def read_prices():
    if os.path.exists(PRICES_FILE):
        try:
            with open(PRICES_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_basket():
    if not os.path.exists(INDEX): return set()
    with open(INDEX) as f:
        src = f.read()
    m = re.search(r"const BASKET=new Set\(\[([^\]]*)\]", src)
    if m:
        return set(re.findall(r"[A-Z]{1,5}", m.group(1)))
    return set()


def spike_universe():
    """Tickers that have ever crossed the confirmed tier, from the archive itself."""
    days, maps = [], {}
    if not os.path.exists(ARCHIVE):
        return set(), ""
    for name in sorted(os.listdir(ARCHIVE)):
        if not name.endswith(".json"):
            continue
        d = name[:-5]
        try:
            with open(os.path.join(ARCHIVE, name)) as f:
                snap = json.load(f)
        except Exception:
            continue
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


def fetch_sa(tk):
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


def yahoo_crumb():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA)]
    for seed in ("https://fc.yahoo.com", f"{Y1}/v8/finance/chart/SPY?range=1d"):
        try: op.open(seed, timeout=20).read()
        except Exception: pass
        try:
            crumb = op.open(f"{Y1}/v1/test/getcrumb", timeout=20).read().decode().strip()
            if crumb and len(crumb) < 24 and "<" not in crumb:
                return op, urllib.parse.quote(crumb, safe="")
        except Exception:
            continue
    return None, None


def fetch_yahoo(tk, op, crumb):
    if not op or not crumb:
        return None
    url = f"{Y1}/v8/finance/chart/{tk}?range=5y&interval=1d&crumb={crumb}"
    try:
        j = json.loads(op.open(url, timeout=20).read().decode())
        r = j["chart"]["result"][0]
        ts = r["timestamp"]
        cl = r["indicators"]["quote"][0]["close"]
        d, c = [], []
        for t, px in zip(ts, cl):
            if px is None: continue
            date_str = dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()
            if date_str < START: continue
            d.append(date_str)
            c.append(round(float(px), 2))
        return {"d": d, "c": c} if len(d) > 20 else None
    except Exception:
        return None


def main():
    dry = "--dry-run" in sys.argv
    old = read_prices()
    basket = get_basket()
    hot, last_archive = spike_universe()

    want = sorted((hot | basket | set(old)) - {""})
    print(f"universe: {len(hot)} ever-spiked + {len(basket)} basket + "
          f"{len(old)} already embedded -> {len(want)} tickers")
    print(f"archive through {last_archive}")

    if dry:
        ends = {}
        for t, v in old.items():
            if v and "d" in v and v["d"]:
                ends[v["d"][-1]] = ends.get(v["d"][-1], 0) + 1
        print("current price cutoffs:", dict(sorted(ends.items())))
        return

    op, crumb = yahoo_crumb()
    if op and crumb:
        print("yahoo crumb acquired for failover")
    else:
        print("yahoo crumb failed, failover unavailable")

    fresh, kept, failed = {}, [], []
    for n, tk in enumerate(want, 1):
        got = fetch_sa(tk)
        if not got:
            got = fetch_yahoo(tk, op, crumb)
            
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
        if v and "d" in v and v["d"]:
            ends[v["d"][-1]] = ends.get(v["d"][-1], 0) + 1
    newest = max(ends) if ends else "?"
    print(f"fetched {len(fresh) - len(kept)} fresh, kept {len(kept)} stale, "
          f"{len(failed)} unavailable{': ' + ', '.join(failed) if failed else ''}")
    print(f"price cutoffs now: {dict(sorted(ends.items()))}")

    os.makedirs(os.path.dirname(PRICES_FILE), exist_ok=True)
    with open(PRICES_FILE, "w") as f:
        json.dump(fresh, f, separators=(",", ":"))
    print(f"data/prices.json updated · prices through {newest} · archive through {last_archive}")


if __name__ == "__main__":
    main()
