#!/usr/bin/env python3
"""Daily WSB attention archiver (v3).
- ApeWisdom: top ~300 (3 pages) for wallstreetbets + all-stocks -> data/apewisdom/{UTC date}.json
  Same UTC day overwrites: the last run of the day (23:45 UTC cron) becomes the daily record;
  the midday run is a fallback so a failed late run still leaves a partial-day snapshot.
- Tradestie revival probe: if api.tradestie.com ever answers again, capture it automatically
  -> data/tradestie/{UTC date}.json
- v3: systemic-risk gauges -> data/risk.json
  * ^SKEW: full 2y daily refresh each run (self-healing; frontend merges with its inline seed)
  * SPY GEX (naive dealer gamma, first 8 expiries, calls +, puts -): appended per UTC day,
    same-day runs overwrite so intraday refreshes stay hour-fresh.
  Yahoo options need the cookie+crumb dance; if it fails, risk.json keeps old values untouched.
Stdlib only. Exit code 0 even on partial failure (Actions should still commit what succeeded).
"""
import json, time, os, sys, math, datetime as dt
import urllib.request, urllib.parse, http.cookiejar

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}
AW_API = "https://apewisdom.io/api/v1.0"
FILTERS = ["wallstreetbets", "all-stocks"]
PAGES = 3
Y1 = "https://query1.finance.yahoo.com"
GEX_EXPIRIES = 8

def get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def fetch_apewisdom():
    out = {}
    for f in FILTERS:
        rows = []
        for page in range(1, PAGES + 1):
            try:
                j = json.loads(get(f"{AW_API}/filter/{f}/page/{page}"))
            except Exception as e:
                print(f"  {f} p{page}: {e}"); break
            items = j.get("results") or []
            if not items: break
            for d in items:
                rows.append({
                    "rank": int(d.get("rank") or 0),
                    "ticker": d.get("ticker"),
                    "name": d.get("name"),
                    "mentions": int(d.get("mentions") or 0),
                    "upvotes": int(d.get("upvotes") or 0),
                    "rank_24h_ago": int(d.get("rank_24h_ago") or 0),
                    "mentions_24h_ago": int(d.get("mentions_24h_ago") or 0),
                })
            time.sleep(0.5)
        out[f] = rows
        print(f"  apewisdom {f}: {len(rows)} rows")
    return out

def probe_tradestie(day_mmddyyyy):
    for url in (f"https://api.tradestie.com/v1/apps/reddit?date={day_mmddyyyy}",
                "https://api.tradestie.com/v1/apps/reddit"):
        try:
            j = json.loads(get(url, timeout=10))
            if isinstance(j, list) and j:
                print(f"  tradestie ALIVE via {url} ({len(j)} rows)")
                return j
        except Exception:
            pass
    print("  tradestie: still dead")
    return None

# ---------------- v3: systemic-risk gauges ----------------

def fetch_skew_2y():
    """^SKEW daily closes, trailing 2y. Returns (dates, values) or (None, None)."""
    try:
        j = json.loads(get(f"{Y1}/v8/finance/chart/%5ESKEW?range=2y&interval=1d"))
        r = j["chart"]["result"][0]
        ts = r["timestamp"]
        cl = r["indicators"]["quote"][0]["close"]
        dates, vals = [], []
        for t, c in zip(ts, cl):
            if c is None: continue
            dates.append(dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat())
            vals.append(round(float(c), 2))
        print(f"  skew: {len(vals)} days, last {dates[-1]} = {vals[-1]}")
        return dates, vals
    except Exception as e:
        print(f"  skew: FAILED ({e})")
        return None, None

def yahoo_crumb():
    """Cookie+crumb dance. Tries fc.yahoo.com (GitHub cloud) then query1 (restricted sandboxes)."""
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = list(UA.items())
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

PHI = lambda x: math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def compute_spy_gex():
    """Naive SPY gamma exposure ($ per 1% move): sum over first GEX_EXPIRIES expiries,
    BS gamma from quoted IV, calls +, puts - (dealer long calls / short puts assumption)."""
    op, crumb = yahoo_crumb()
    if not op:
        print("  gex: crumb dance failed, skipping"); return None
    def oget(url):
        return json.loads(op.open(url, timeout=25).read().decode("utf-8", "replace"))
    try:
        base = oget(f"{Y1}/v7/finance/options/SPY?crumb={crumb}")
        res = base["optionChain"]["result"][0]
        S = float(res["quote"]["regularMarketPrice"])
        expiries = res.get("expirationDates", [])[:GEX_EXPIRIES]
    except Exception as e:
        print(f"  gex: chain root FAILED ({e})"); return None
    now = time.time()
    gex, n_used = 0.0, 0
    for ets in expiries:
        try:
            o = oget(f"{Y1}/v7/finance/options/SPY?date={ets}&crumb={crumb}")["optionChain"]["result"][0]
        except Exception as e:
            print(f"  gex: expiry {ets} skipped ({e})"); continue
        T = max((ets - now) / (365.0 * 86400.0), 0.5 / 365.0)
        for side, sgn in (("calls", 1), ("puts", -1)):
            for c in o["options"][0].get(side, []):
                oi = c.get("openInterest") or 0
                iv = c.get("impliedVolatility") or 0
                K = c.get("strike") or 0
                if oi <= 0 or iv < 0.01 or iv > 5 or K <= 0: continue
                d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
                gamma = PHI(d1) / (S * iv * math.sqrt(T))
                gex += sgn * oi * gamma * S * S * 0.01 * 100
                n_used += 1
        time.sleep(0.4)
    if n_used == 0:
        print("  gex: no usable contracts"); return None
    print(f"  gex: SPY spot {S:.0f}, {n_used} contracts, {len(expiries)} expiries"
          f" -> ${gex/1e9:+.2f}B / 1% ({'positive gamma: vol suppressed' if gex > 0 else 'NEGATIVE gamma: vol amplified, fragile'})")
    return round(gex / 1e9, 2)

def update_risk(root, day):
    path = f"{root}/data/risk.json"
    old = {}
    if os.path.exists(path):
        try: old = json.load(open(path))
        except Exception: old = {}
    R = {"dates": old.get("dates") or [], "skew": old.get("skew") or [],
         "gex_dates": old.get("gex_dates") or [], "gex_bn": old.get("gex_bn") or []}
    d, v = fetch_skew_2y()
    if d: R["dates"], R["skew"] = d, v            # full self-healing rewrite
    g = compute_spy_gex()
    if g is not None:
        if R["gex_dates"] and R["gex_dates"][-1] == day:
            R["gex_bn"][-1] = g                    # same-day overwrite (hourly cron)
        else:
            R["gex_dates"].append(day); R["gex_bn"].append(g)
    if not R["dates"] and g is None:
        print("  risk: nothing fetched, not writing"); return
    os.makedirs(f"{root}/data", exist_ok=True)
    json.dump(R, open(path, "w"), separators=(",", ":"))
    print(f"  risk.json: {len(R['dates'])} skew days, {len(R['gex_dates'])} gex days")

# -----------------------------------------------------------

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    now = dt.datetime.now(dt.timezone.utc)
    day = now.date().isoformat()

    aw = fetch_apewisdom()
    if any(aw.values()):
        os.makedirs(f"{root}/data/apewisdom", exist_ok=True)
        json.dump({"fetched_at": now.isoformat(), "filters": aw},
                  open(f"{root}/data/apewisdom/{day}.json", "w"))
    else:
        print("  apewisdom: nothing fetched, not writing")

    ts = probe_tradestie(now.strftime("%m-%d-%Y"))
    if ts:
        os.makedirs(f"{root}/data/tradestie", exist_ok=True)
        json.dump({"fetched_at": now.isoformat(), "rows": ts},
                  open(f"{root}/data/tradestie/{day}.json", "w"))

    update_risk(root, day)
    print("done")

if __name__ == "__main__":
    main()
