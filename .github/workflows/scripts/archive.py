
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

    ts = probe_tradestie(now.strftime("%m-%d-%Y"))
    if ts:
        os.makedirs(f"{root}/data/tradestie", exist_ok=True)
        json.dump({"fetched_at": now.isoformat(), "rows": ts},
                  open(f"{root}/data/tradestie/{day}.json", "w"))
    print("done")

if __name__ == "__main__":
    main()
