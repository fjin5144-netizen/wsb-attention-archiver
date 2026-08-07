#!/usr/bin/env python3
"""Long price history for the rank study -> data/prices_hist.json

data/prices.json starts 2026-04-01, which is all the daily archive needs and none of
what the Wayback backfill needs — that reaches 2021. Separate file rather than
widening the existing one: prices.json is refreshed every day and read by the site on
every load, and quintupling it to serve one offline study would make every visitor pay
for it.

Universe is derived, not listed: whichever tickers actually held a top-3 spot on some
day in either dataset.

    python3 scripts/prices_hist.py            # fetch what is missing
    python3 scripts/prices_hist.py --refresh  # refetch everything
"""
import json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "prices_hist.json")
START = "2021-04-01"          # a month before the earliest crawl, for a pre-window
TOP_N = 3
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def board(path, top=TOP_N):
    with open(path) as f:
        rows = (json.load(f).get("filters") or {}).get("wallstreetbets") or []
    return [r["ticker"] for r in rows[:top]]


def universe():
    tks = set()
    wb = os.path.join(ROOT, "data", "wayback")
    if os.path.isdir(wb):
        try:
            with open(os.path.join(wb, "quality.json")) as f:
                bad = set(json.load(f)["suspect"])
        except Exception:
            bad = set()
        for n in os.listdir(wb):
            if re.fullmatch(r"\d{4}-\d\d-\d\d\.json", n) and n[:-5] not in bad:
                tks |= set(board(os.path.join(wb, n)))
    arch = os.path.join(ROOT, "data", "apewisdom")
    for n in os.listdir(arch):
        if re.fullmatch(r"\d{4}-\d\d-\d\d\.json", n):
            tks |= set(board(os.path.join(arch, n)))
    return sorted(t for t in tks if re.fullmatch(r"[A-Z][A-Z.\-]{0,5}", t))


def fetch(tk):
    url = f"https://stockanalysis.com/api/symbol/s/{tk}/history?range=10Y&period=Daily"
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
        d.append(b["t"])
        c.append(round(float(b["c"]), 4))
    return {"d": d, "c": c} if len(d) > 60 else None


def main():
    old = {}
    if os.path.exists(OUT) and "--refresh" not in sys.argv:
        with open(OUT) as f:
            old = json.load(f)
    want = universe()
    todo = [t for t in want if t not in old]
    print(f"universe {len(want)} tickers · have {len(old)} · fetching {len(todo)}")

    got = failed = 0
    for n, tk in enumerate(todo, 1):
        v = fetch(tk)
        if v:
            old[tk] = v
            got += 1
        else:
            failed += 1
        if n % 25 == 0 or n == len(todo):
            print(f"  {n}/{len(todo)}  ok={got} failed={failed}", flush=True)
        time.sleep(0.6)

    with open(OUT, "w") as f:
        json.dump(old, f, separators=(",", ":"))
    spans = [v["d"][0] for v in old.values() if v.get("d")]
    print(f"data/prices_hist.json · {len(old)} tickers · earliest bar "
          f"{min(spans) if spans else '-'} · {os.path.getsize(OUT) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
