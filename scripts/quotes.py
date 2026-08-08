#!/usr/bin/env python3
"""Archive the live board price alongside each attention read -> data/quotes/

The daily price pack holds session closes. The attention snapshot is read at whatever
hour a run lands, so pairing a mentions count with a close pairs it with a different
moment. This records the price *at the moment of the read*, which is the only version
of "what was it worth when this many people were talking about it" that is actually
simultaneous.

It also answers the failure the rest of the pipeline could not. Everything already
collected survives a source dying — it is committed — but the browser's live quote had
one source and no memory: if stockanalysis goes the way of tradestie, the backfill repo
and stooq, the panel would simply have nothing. With this, the site falls back to the
last archived quote and says how old it is.

CNBC rather than stockanalysis because it batches: forty tickers in one request,
against one request each. Same numbers where they overlap — SPCX 133.11, +18.19,
+15.83%, previous close 114.92, after-hours 134.10 on both.

    python3 scripts/quotes.py            # append one reading for today
    python3 scripts/quotes.py --dry-run  # fetch and print, write nothing
"""
import json, os, re, subprocess, sys, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "data", "apewisdom")
OUT = os.path.join(ROOT, "data", "quotes")
TOP_N = 40           # one request covers this comfortably; 40 returned 40
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CNBC = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
        "?symbols={syms}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")


def today_board():
    """The tickers worth pricing: whatever is loudest on the newest snapshot."""
    days = sorted(n for n in os.listdir(ARCHIVE) if re.fullmatch(r"\d{4}-\d\d-\d\d\.json", n))
    if not days:
        return "", []
    with open(os.path.join(ARCHIVE, days[-1])) as f:
        snap = json.load(f)
    rows = (snap.get("filters") or {}).get("wallstreetbets") or []
    tks = [r["ticker"] for r in rows[:TOP_N]
           if re.fullmatch(r"[A-Z][A-Z.\-]{0,5}", r.get("ticker", ""))]
    return days[-1][:-5], tks


def num(v):
    """CNBC formats everything as a display string: '+15.83%', '1,234.50', 'UNCH'."""
    if v is None:
        return None
    t = str(v).replace(",", "").replace("%", "").replace("+", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def fetch(tickers):
    if not tickers:
        return {}
    url = CNBC.format(syms="|".join(tickers))
    r = subprocess.run(["curl", "-s", "--max-time", "40", "-H", f"User-Agent: {UA}", url],
                       capture_output=True, text=True)
    try:
        rows = json.loads(r.stdout)["FormattedQuoteResult"]["FormattedQuote"]
    except Exception:
        return {}
    if isinstance(rows, dict):
        rows = [rows]
    out = {}
    for q in rows:
        tk = q.get("symbol")
        p = num(q.get("last"))
        if not tk or p is None:
            continue
        e = q.get("ExtendedMktQuote") or {}
        row = {"p": p, "chg": num(q.get("change")), "pct": num(q.get("change_pct")),
               "prev": num(q.get("previous_day_closing")),
               "at": q.get("last_time"), "status": q.get("curmktstatus")}
        if num(e.get("last")) is not None:
            row["ext"] = {"p": num(e.get("last")), "pct": num(e.get("change_pct")),
                          "at": e.get("last_time")}
        out[tk] = row
    return out


def main():
    day, tks = today_board()
    if not tks:
        print("no board to price")
        return
    quotes = fetch(tks)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reading = {"t": now, "n": len(quotes), "quotes": quotes}

    if "--dry-run" in sys.argv or not quotes:
        print(f"{day}: {len(quotes)}/{len(tks)} priced at {now}")
        for tk in tks[:5]:
            q = quotes.get(tk)
            print(f"  {tk:6s} {q['p'] if q else '—'}"
                  + (f"  {q['pct']:+.2f}%  {q['status']}" if q else ""))
        return

    # One immutable file per reading, not one growing file per day.
    #
    # Appending to a day file means rewriting it on every run, and git keeps every
    # version: fourteen runs a day would store 7 + 14 + 21 … + 98 KB of blobs, about
    # 270 MB a year against a repo that is currently 15. Written once and never
    # touched again, the same data is ~35 MB a year.
    daydir = os.path.join(OUT, day)
    os.makedirs(daydir, exist_ok=True)
    stamp = now[11:16].replace(":", "")
    with open(os.path.join(daydir, f"{stamp}.json"), "w") as f:
        json.dump(reading, f, separators=(",", ":"))

    # The site only ever wants the newest one, and cannot list a directory on Pages.
    with open(os.path.join(OUT, "latest.json"), "w") as f:
        json.dump({"day": day, **reading}, f, separators=(",", ":"))

    n = len([x for x in os.listdir(daydir) if x.endswith(".json")])
    print(f"data/quotes/{day}/{stamp}.json · reading {n} today "
          f"· {len(quotes)}/{len(tks)} priced")


if __name__ == "__main__":
    main()
