#!/usr/bin/env python3
"""Daily WSB attention archiver (v3.3).
- ApeWisdom: top ~300 (3 pages) for wallstreetbets + all-stocks -> data/apewisdom/{UTC date}.json
  Same UTC day overwrites: the last run of the day (23:45 UTC cron) becomes the daily record;
  the midday run is a fallback so a failed late run still leaves a partial-day snapshot.
- Tradestie revival probe: if api.tradestie.com ever answers again, capture it automatically
  -> data/tradestie/{UTC date}.json
- v3.1: systemic-risk gauges -> data/risk.json
  * ^SKEW: full 2y daily refresh each run (self-healing; frontend merges with its inline seed)
  * SPY GEX (naive dealer gamma, nearest 8 expiries, calls +, puts -): appended per UTC day,
    same-day runs overwrite so intraday refreshes stay hour-fresh.
- v3.2: daily refresh of data/DIX.csv from SqueezeMetrics (official DIX/GEX history,
  2011->present); on fetch failure, promotes a locally committed copy
  (data/apewisdom/DIX.csv) once, else keeps the existing file untouched.
  Also refreshes data/spy.json (SPY daily closes; stooq primary, Yahoo fallback)
  for the dashboard's SPY-vs-200dma regime gate.
  Sources: CBOE public CDN primary (GitHub-runner friendly), Yahoo fallback
  (Yahoo rejects Azure/GitHub IPs as of 2026-07). Every run also writes
  data/risk_status.json with per-source diagnostics for remote debugging.
Stdlib only. Exit code 0 even on partial failure (Actions should still commit what succeeded).
"""
import json, time, os, sys, math, datetime as dt
import urllib.request, urllib.parse, http.cookiejar

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}
AW_API = "https://apewisdom.io/api/v1.0"
FILTERS = ["wallstreetbets", "all-stocks"]
PAGES = 5   # top-500: mania periods raise the rank-300 mention floor; wider net avoids censoring (empty pages break gracefully)
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

# ---------------- v3.1: systemic-risk gauges ----------------
CBOE = "https://cdn.cboe.com/api/global"
STATUS = {"ver": "3.3"}

def get_retry(url, tries=3, timeout=30):
    last = None
    for i in range(tries):
        try:
            return get(url, timeout=timeout)
        except Exception as e:
            last = e; time.sleep(2 + 4 * i)
    raise last

def cboe_skew_2y():
    """SKEW daily history CSV from CBOE. Header row located by 'DATE'; last numeric col = value."""
    raw = get_retry(f"{CBOE}/us_indices/daily_prices/SKEW_History.csv", timeout=40)
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    start = next(i for i, l in enumerate(lines) if l.upper().startswith("DATE"))
    cutoff = (dt.date.today() - dt.timedelta(days=730)).isoformat()
    dates, vals = [], []
    for l in lines[start + 1:]:
        p = [x.strip() for x in l.split(",")]
        if len(p) < 2: continue
        d = p[0]
        try:
            if "/" in d:  # MM/DD/YYYY
                m, dd, y = d.split("/"); d = f"{y}-{int(m):02d}-{int(dd):02d}"
            v = float(p[-1])
        except Exception:
            continue
        if d >= cutoff:
            dates.append(d); vals.append(round(v, 2))
    if len(vals) < 100: raise ValueError(f"cboe skew too short: {len(vals)}")
    return dates, vals

def _parse_occ(code, root="SPY"):
    """SPY260717C00620000 -> (expiry_date, 'C'/'P', strike) or None."""
    s = code.strip().upper()
    if not s.startswith(root): return None
    s = s[len(root):]
    if len(s) < 15: return None
    try:
        exp = dt.date(2000 + int(s[0:2]), int(s[2:4]), int(s[4:6]))
        cp = s[6]
        strike = int(s[7:15]) / 1000.0
        if cp not in "CP" or strike <= 0: return None
        return exp, cp, strike
    except Exception:
        return None

def cboe_gex():
    """Naive SPY GEX from CBOE delayed quotes. Uses provided per-contract gamma when
    present, else BS gamma from IV. Nearest GEX_EXPIRIES expiries, calls +, puts -."""
    j = json.loads(get_retry(f"{CBOE}/delayed_quotes/options/SPY.json", timeout=60))
    data = j.get("data") or {}
    S = float(data.get("current_price") or data.get("close") or 0)
    if S <= 0: raise ValueError("cboe: no spot")
    rows = []
    for c in data.get("options") or []:
        p = _parse_occ(c.get("option") or "")
        if p: rows.append((p, c))
    if not rows: raise ValueError("cboe: no parsable contracts")
    today = dt.date.today()
    expiries = sorted({p[0] for p, _ in rows if p[0] >= today})[:GEX_EXPIRIES]
    if not expiries: raise ValueError("cboe: no future expiries")
    keep = set(expiries)
    gex, n_used = 0.0, 0
    for (exp, cp, K), c in rows:
        if exp not in keep: continue
        oi = c.get("open_interest") or c.get("oi") or 0
        iv = c.get("iv") or c.get("implied_volatility") or 0
        if iv > 5: iv = iv / 100.0  # some feeds ship percent
        gamma = c.get("gamma") or 0
        if oi <= 0: continue
        if not gamma or gamma <= 0:
            if iv < 0.01 or iv > 5: continue
            T = max((exp - today).days / 365.0, 0.5 / 365.0)
            d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
            gamma = PHI(d1) / (S * iv * math.sqrt(T))
        gex += (1 if cp == "C" else -1) * oi * gamma * S * S * 0.01 * 100
        n_used += 1
    if n_used < 50: raise ValueError(f"cboe: only {n_used} usable contracts")
    print(f"  gex[cboe]: spot {S:.0f}, {n_used} contracts, {len(expiries)} expiries -> ${gex/1e9:+.2f}B / 1%")
    return round(gex / 1e9, 2)


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


SM_URL = "https://squeezemetrics.com/monitor/static/DIX.csv"

def refresh_dix(root):
    """Keep data/DIX.csv fresh. Frontend reads it directly for the 2y GEX/DIX charts."""
    dst = f"{root}/data/DIX.csv"
    try:
        txt = get_retry(SM_URL, timeout=40)
        lines = txt.strip().splitlines()
        if not lines or not lines[0].lower().startswith("date,price,dix,gex") or len(lines) < 1000:
            raise ValueError(f"unexpected payload ({len(lines)} lines)")
        last = lines[-1].split(",")[0]
        if (dt.date.today() - dt.date.fromisoformat(last)).days > 14:
            raise ValueError(f"stale feed, last {last}")
        os.makedirs(f"{root}/data", exist_ok=True)
        open(dst, "w", newline="").write(txt if txt.endswith("\n") else txt + "\n")
        STATUS["dix"] = f"ok squeezemetrics ({len(lines)-1} rows, last {last})"
        print(f"  dix: refreshed from squeezemetrics, last {last}")
    except Exception as e1:
        alt = f"{root}/data/apewisdom/DIX.csv"
        if not os.path.exists(dst) and os.path.exists(alt):
            os.makedirs(f"{root}/data", exist_ok=True)
            open(dst, "w", newline="").write(open(alt).read())
            STATUS["dix"] = f"promoted local seed; fetch: {e1}"
            print("  dix: fetch failed, promoted data/apewisdom/DIX.csv -> data/DIX.csv")
        else:
            STATUS["dix"] = f"kept existing; fetch: {e1}"
            print(f"  dix: fetch failed ({e1}), keeping existing file")

def refresh_spy(root):
    """data/spy.json <- SPY daily closes {dates, close}. stooq primary, Yahoo fallback.
    Feeds the dashboard's SPY-vs-200dma regime gate (merged over its inline seed)."""
    dates = closes = None
    try:
        raw = get_retry("https://stooq.com/q/d/l/?s=spy.us&i=d", timeout=40)
        rows = [l.split(",") for l in raw.strip().splitlines()[1:] if l.count(",") >= 4]
        rows = [(r[0], float(r[4])) for r in rows[-520:]]
        assert len(rows) > 300 and rows[-1][1] > 0, f"thin stooq payload ({len(rows)})"
        dates, closes = [r[0] for r in rows], [round(r[1], 2) for r in rows]
        STATUS["spy"] = f"ok stooq ({len(dates)}d, last {dates[-1]} = {closes[-1]:.0f})"
    except Exception as e1:
        try:
            j = json.loads(get_retry(f"{Y1}/v8/finance/chart/SPY?range=2y&interval=1d"))
            r = j["chart"]["result"][0]
            dates, closes = [], []
            for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
                if c is None: continue
                dates.append(dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat())
                closes.append(round(float(c), 2))
            if len(dates) <= 300: raise ValueError(f"thin yahoo payload ({len(dates)})")
            STATUS["spy"] = f"ok yahoo ({len(dates)}d); stooq: {e1}"
        except Exception as e2:
            try:
                j = json.loads(get_retry(f"{CBOE}/delayed_quotes/quotes/SPY.json", timeout=30))
                dd = j.get("data") or {}
                px = float(dd.get("current_price") or dd.get("close") or 0)
                if px <= 0: raise ValueError("cboe: no spot")
                p = f"{root}/data/spy.json"
                cur = {"dates": [], "close": []}
                if os.path.exists(p):
                    try: cur = json.load(open(p))
                    except Exception: pass
                today = dt.date.today().isoformat()
                if cur["dates"] and cur["dates"][-1] == today:
                    cur["close"][-1] = round(px, 2)
                else:
                    cur["dates"].append(today); cur["close"].append(round(px, 2))
                os.makedirs(f"{root}/data", exist_ok=True)
                json.dump(cur, open(p, "w"), separators=(",", ":"))
                STATUS["spy"] = f"ok cboe spot-append ({today} = {px:.0f}); stooq: {str(e1)[:60]}; yahoo: {str(e2)[:60]}"
                print(f"  spy: {STATUS['spy']}")
                return
            except Exception as e3:
                dates = None
                STATUS["spy"] = f"FAIL stooq: {str(e1)[:60]}; yahoo: {str(e2)[:60]}; cboe: {str(e3)[:60]}"
    if dates:
        os.makedirs(f"{root}/data", exist_ok=True)
        json.dump({"dates": dates, "close": closes}, open(f"{root}/data/spy.json", "w"),
                  separators=(",", ":"))
    print(f"  spy: {STATUS['spy']}")

def update_risk(root, day):
    refresh_dix(root)
    refresh_spy(root)
    path = f"{root}/data/risk.json"
    old = {}
    if os.path.exists(path):
        try: old = json.load(open(path))
        except Exception: old = {}
    R = {"dates": old.get("dates") or [], "skew": old.get("skew") or [],
         "gex_dates": old.get("gex_dates") or [], "gex_bn": old.get("gex_bn") or []}
    d = v = None
    try:
        d, v = cboe_skew_2y(); STATUS["skew"] = f"ok cboe ({len(v)}d)"
    except Exception as e1:
        try:
            d, v = fetch_skew_2y()
            STATUS["skew"] = f"ok yahoo ({len(v)}d); cboe: {e1}" if d else f"FAIL cboe: {e1}; yahoo: none"
        except Exception as e2:
            STATUS["skew"] = f"FAIL cboe: {e1}; yahoo: {e2}"
    if d: R["dates"], R["skew"] = d, v            # full self-healing rewrite
    g = None
    try:
        g = cboe_gex(); STATUS["gex"] = f"ok cboe ({g:+.2f}B)"
    except Exception as e1:
        try:
            g = compute_spy_gex()
            STATUS["gex"] = f"ok yahoo ({g:+.2f}B); cboe: {e1}" if g is not None else f"FAIL cboe: {e1}; yahoo: none"
        except Exception as e2:
            STATUS["gex"] = f"FAIL cboe: {e1}; yahoo: {e2}"
    if g is not None:
        if R["gex_dates"] and R["gex_dates"][-1] == day:
            R["gex_bn"][-1] = g                    # same-day overwrite (hourly cron)
        else:
            R["gex_dates"].append(day); R["gex_bn"].append(g)
    os.makedirs(f"{root}/data", exist_ok=True)
    STATUS["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    json.dump(STATUS, open(f"{root}/data/risk_status.json", "w"), indent=1)
    print(f"  status: skew={STATUS.get('skew')} | gex={STATUS.get('gex')}")
    if not R["dates"] and g is None:
        print("  risk: nothing fetched, risk.json untouched"); return
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
