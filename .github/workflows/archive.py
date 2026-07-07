#!/usr/bin/env python3
"""Daily WSB attention archiver.
- ApeWisdom: top ~300 (3 pages) for wallstreetbets + all-stocks -> data/apewisdom/{UTC date}.json
  Same UTC day overwrites: the last run of the day (23:45 UTC cron) becomes the daily record;
  the midday run is a fallback so a failed late run still leaves a partial-day snapshot.
- Tradestie revival probe: if api.tradestie.com ever answers again, capture it automatically
  -> data/tradestie/{UTC date}.json
Stdlib only. Exit code 0 even on partial failure (Actions should still commit what succeeded).
"""
import json, time, os, sys, datetime as dt
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}
AW_API = "https://apewisdom.io/api/v1.0"
FILTERS = ["wallstreetbets", "all-stocks"]
PAGES = 3

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

    # SPY 每日收盘(体温计用):追加进 data/spy.json,保留最近 500 条
    try:
        j = json.loads(get("https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=5d&interval=1d"))
        res = j["chart"]["result"][0]
        import datetime as _dt
        pairs = [( _dt.datetime.fromtimestamp(s, _dt.timezone.utc).date().isoformat(), round(c,2) )
                 for s, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c]
        p = f"{root}/data/spy.json"
        spy = json.load(open(p)) if os.path.exists(p) else {"dates": [], "close": []}
        for dd, cc in pairs:
            if dd in spy["dates"]:
                spy["close"][spy["dates"].index(dd)] = cc
            else:
                spy["dates"].append(dd); spy["close"].append(cc)
        order = sorted(range(len(spy["dates"])), key=lambda k: spy["dates"][k])[-500:]
        spy = {"dates": [spy["dates"][k] for k in order], "close": [spy["close"][k] for k in order]}
        json.dump(spy, open(p, "w"))
        print(f"  spy: {spy['dates'][-1]} close {spy['close'][-1]}")
    except Exception as e:
        print(f"  spy: {e}")

    # 系统性风险仪表:SKEW(Yahoo 直取)+ SPY GEX(期权链自算,朴素口径 calls正/puts负)
    try:
        import math, http.cookiejar
        p = f"{root}/data/risk.json"
        risk = json.load(open(p)) if os.path.exists(p) else {"dates": [], "skew": [], "gex_dates": [], "gex_bn": []}
        # SKEW
        j = json.loads(get("https://query1.finance.yahoo.com/v8/finance/chart/%5ESKEW?range=5d&interval=1d"))
        res = j["chart"]["result"][0]
        import datetime as _dt
        for s, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
            if not c: continue
            dd = _dt.datetime.fromtimestamp(s, _dt.timezone.utc).date().isoformat()
            if dd in risk["dates"]: risk["skew"][risk["dates"].index(dd)] = round(c, 2)
            else: risk["dates"].append(dd); risk["skew"].append(round(c, 2))
        print(f"  skew: {risk['dates'][-1]} = {risk['skew'][-1]}")
        # GEX(需要 cookie+crumb;失败则跳过,不影响其他数据)
        try:
            cj = http.cookiejar.CookieJar()
            op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            def yop(u):
                return op.open(urllib.request.Request(u, headers=UA), timeout=25).read().decode("utf-8", "replace")
            try: yop("https://fc.yahoo.com")
            except Exception: pass                       # 404 正常,cookie 已落袋
            crumb = yop("https://query1.finance.yahoo.com/v1/test/getcrumb").strip()
            ob = json.loads(yop(f"https://query1.finance.yahoo.com/v7/finance/options/SPY?crumb={crumb}"))["optionChain"]["result"][0]
            S = ob["quote"]["regularMarketPrice"]; exps = ob["expirationDates"][:8]
            PHI = lambda d: math.exp(-d*d/2)/math.sqrt(2*math.pi)
            gex = 0.0
            for k, e in enumerate(exps):
                o = ob if k == 0 else json.loads(yop(f"https://query1.finance.yahoo.com/v7/finance/options/SPY?date={e}&crumb={crumb}"))["optionChain"]["result"][0]
                T = max((e - time.time())/86400, 0.5)/365
                for side, sgn in (("calls", 1), ("puts", -1)):
                    for c in o["options"][0][side]:
                        oi = c.get("openInterest") or 0; iv = c.get("impliedVolatility") or 0; K = c["strike"]
                        if oi <= 0 or iv < 0.01 or iv > 5: continue
                        d1 = (math.log(S/K) + 0.5*iv*iv*T)/(iv*math.sqrt(T))
                        gex += sgn * oi * (PHI(d1)/(S*iv*math.sqrt(T))) * S*S*0.01*100
                time.sleep(0.4)
            dd = now.date().isoformat()
            if dd in risk["gex_dates"]: risk["gex_bn"][risk["gex_dates"].index(dd)] = round(gex/1e9, 2)
            else: risk["gex_dates"].append(dd); risk["gex_bn"].append(round(gex/1e9, 2))
            print(f"  gex: {dd} = {gex/1e9:+.2f}B/1%")
        except Exception as e:
            print(f"  gex skipped: {e}")
        for a, b in (("dates","skew"), ("gex_dates","gex_bn")):
            risk[a], risk[b] = risk[a][-600:], risk[b][-600:]
        json.dump(risk, open(p, "w"))
    except Exception as e:
        print(f"  risk gauges: {e}")

    # SqueezeMetrics 官方 DIX/GEX 历史文件(免费公开下载),每日整份刷新
    try:
        got = None
        for u in ("https://squeezemetrics.com/monitor/static/DIX.csv",
                  "https://squeezemetrics.com/monitor/static/dix.csv"):
            try:
                body = get(u, timeout=40)
                if body.startswith("date,price,dix,gex") and len(body) > 100000:
                    got = (u, body); break
            except Exception:
                pass
        if got:
            open(f"{root}/data/DIX.csv", "w").write(got[1])
            last = got[1].strip().splitlines()[-1].split(",")
            print(f"  dix/gex: {last[0]} dix={float(last[2]):.3f} gex={float(last[3])/1e9:+.2f}B  ({got[0]})")
        else:
            print("  dix/gex: 所有候选地址均失败,保留旧文件")
    except Exception as e:
        print(f"  dix/gex: {e}")

    ts = probe_tradestie(now.strftime("%m-%d-%Y"))
    if ts:
        os.makedirs(f"{root}/data/tradestie", exist_ok=True)
        json.dump({"fetched_at": now.isoformat(), "rows": ts},
                  open(f"{root}/data/tradestie/{day}.json", "w"))
    print("done")

if __name__ == "__main__":
    main()
