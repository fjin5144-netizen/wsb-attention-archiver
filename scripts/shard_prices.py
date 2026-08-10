#!/usr/bin/env python3
"""A price history for every ticker the archive has ever seen -> data/px/

prices.json covers 155 tickers and prices_hist.json 143, both of them the set that
spikes under the default definition, because refresh_prices.py fetches a ticker when it
becomes an event. That is the right rule for the research — the finding is measured on
that pack and nothing else — but it means 70% of a 500-deep board opens to a chart with
mentions and no line, and a name reached through a rank-move filter like Deep risers
usually has no price at all.

So: everything, in a form the browser can afford.

Two decisions carry the design.

  * Dates are stored once. `{"d":["2021-04-01",...],"c":[...]}` spends 65% of itself on
    a date array that is byte-identical across every ticker — 1,868 copies of the same
    17 KB. A shared index plus `{"i":<first index>,"c":[...]}` takes the full universe
    from 45 MB to 14 MB. SPY is the calendar: checked against all 143 tickers already
    held, not one of them trades on a session SPY does not, so an index built from SPY
    never has to be inserted into, which would invalidate every shard's `i`.

  * One file per ticker, written once. The daily job rewrites prices.json — 264 KB a
    day, and git keeps every version. A 14 MB pack rewritten daily would be 5 GB a year.
    Shards are history: written when a ticker first appears, refreshed only when asked
    for by --refresh, and otherwise never touched. The recent window stays prices.json's
    job, and the live quote covers today.

Tickers that do not resolve are recorded in data/px/_missing.json rather than retried
every run, so the page can distinguish "we looked and there is nothing" from "not fetched
yet" — which had been the same blank.

Fourteen of 1,872 land there, and they are not the junk the first draft of this comment
assumed. Retried three times each against a control group that answered normally, so the
400s are the endpoint's and not a rate limit: BK returns nothing while BNY returns five
years, because Bank of New York Mellon retired the one for the other. APE converted into
AMC; ABB and CBD delisted their ADRs. The archive keeps mentions of tickers that have
stopped existing, and the price source only knows what trades today. Some of the fourteen
are genuinely not securities — AUD, CN, CO — but the file cannot tell them apart, so
nothing downstream claims to.

**Display only.** Nothing here reaches prices.json, events.json or finding.json. The
research pack stays the research pack; these exist so a chart can show a line.

    python3 scripts/shard_prices.py --limit 200     # fetch 200 missing tickers
    python3 scripts/shard_prices.py                 # everything missing
    python3 scripts/shard_prices.py --refresh AAPL  # re-fetch specific tickers
    python3 scripts/shard_prices.py --status        # what is covered, fetch nothing
"""
import json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "data", "apewisdom")
OUT = os.path.join(ROOT, "data", "px")
CAL = "SPY"                  # the calendar every US equity session appears in
PAUSE = 0.35                 # ~3/s against one host, for a job that runs unattended
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def universe():
    """Every ticker on any archived board — the same rule precompute_events.py uses.

    Not the keys of prices.json, which is the set this script exists to get past.
    """
    tks = set()
    for name in sorted(os.listdir(ARCHIVE)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(ARCHIVE, name)) as f:
            snap = json.load(f)
        for r in (snap.get("filters") or {}).get("wallstreetbets") or []:
            t = r.get("ticker", "")
            if re.fullmatch(r"[A-Z][A-Z.\-]{0,5}", t):
                tks.add(t)
    return sorted(tks)


def fetch(tk):
    """Daily closes for the last five years, or None if the symbol does not resolve."""
    url = f"https://stockanalysis.com/api/symbol/s/{tk}/history?range=5Y&period=Daily"
    r = subprocess.run(["curl", "-s", "--max-time", "30", "-H", f"User-Agent: {UA}", url],
                       capture_output=True, text=True)
    try:
        rows = json.loads(r.stdout)["data"]
    except Exception:
        return None
    # Bars are objects keyed t/c, not positional arrays — the same shape refresh_prices
    # already parses. Kept in step with it deliberately: two readers of one endpoint that
    # disagree about its shape is how a pack ends up half-populated and looking fine.
    out = {}
    for b in rows or []:
        try:
            d, c = b["t"][:10], float(b["c"])
        except (TypeError, ValueError, KeyError):
            continue
        if re.fullmatch(r"\d{4}-\d\d-\d\d", d) and c > 0:
            out[d] = round(c, 2)
    return out if len(out) > 20 else None


def load(path, dflt):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return dflt


def main():
    os.makedirs(OUT, exist_ok=True)
    dates_path = os.path.join(OUT, "_dates.json")
    missing_path = os.path.join(OUT, "_missing.json")

    dates = load(dates_path, None)
    if not dates:
        cal = fetch(CAL)
        if not cal:
            sys.exit(f"cannot build the calendar: {CAL} did not resolve")
        dates = sorted(cal)
        with open(dates_path, "w") as f:
            json.dump(dates, f, separators=(",", ":"))
        print(f"calendar: {len(dates)} sessions {dates[0]}..{dates[-1]}")
    idx = {d: i for i, d in enumerate(dates)}

    missing = load(missing_path, {})
    uni = universe()
    have = {n[:-5] for n in os.listdir(OUT) if n.endswith(".json") and not n.startswith("_")}

    if "--status" in sys.argv:
        todo = [t for t in uni if t not in have and t not in missing]
        print(f"universe {len(uni)} · sharded {len(have)} · unresolvable {len(missing)} "
              f"· still to fetch {len(todo)}")
        size = sum(os.path.getsize(os.path.join(OUT, n)) for n in os.listdir(OUT))
        print(f"data/px is {size/1e6:.1f} MB")
        return

    if "--retry-missing" in sys.argv:
        # A 400 during a long unattended run is ambiguous: the endpoint answering "no such
        # symbol", or it having had enough of us. The first is permanent and the second is
        # not, and the site says something different about each — so the list is re-asked
        # with a control alongside it. If the control fails, this run proves nothing and
        # says so rather than confirming the entries.
        ctrl = fetch("AAPL")
        if not ctrl:
            sys.exit("control ticker AAPL did not resolve either — the source is refusing "
                     "us, so nothing can be concluded about the missing list right now")
        todo = sorted(missing)
        print(f"re-asking {len(todo)} unresolved (control AAPL: {len(ctrl)} bars)")
        for tk in list(todo):
            if fetch(tk):
                del missing[tk]
                print(f"  {tk} resolves after all")
            time.sleep(PAUSE)
        todo = [t for t in todo if t not in missing]

    if "--refresh" in sys.argv:
        todo = [a.upper() for a in sys.argv[sys.argv.index("--refresh") + 1:]
                if not a.startswith("-")]
    else:
        todo = [t for t in uni if t not in have and t not in missing]
        if "--limit" in sys.argv:
            todo = todo[:int(sys.argv[sys.argv.index("--limit") + 1])]

    print(f"{len(todo)} to fetch · {len(have)} already sharded · {len(missing)} known bad")
    ok = bad = off = 0
    for n, tk in enumerate(todo, 1):
        series = fetch(tk)
        time.sleep(PAUSE)
        if not series:
            missing[tk] = "unresolved"
            bad += 1
            continue
        pos = sorted((idx[d], c) for d, c in series.items() if d in idx)
        # A session the calendar does not have would mean SPY missed a trading day, which
        # would make `i` mean different things in different shards. Counted, not silently
        # dropped.
        off += len(series) - len(pos)
        if not pos:
            missing[tk] = "no session in the calendar"
            bad += 1
            continue
        lo = pos[0][0]
        arr = [None] * (pos[-1][0] - lo + 1)
        for p, c in pos:
            arr[p - lo] = c
        with open(os.path.join(OUT, f"{tk}.json"), "w") as f:
            json.dump({"i": lo, "c": arr}, f, separators=(",", ":"))
        ok += 1
        if n % 100 == 0:
            print(f"  {n}/{len(todo)} · {ok} written · {bad} unresolved", flush=True)

    with open(missing_path, "w") as f:
        json.dump(missing, f, indent=0, sort_keys=True)
    size = sum(os.path.getsize(os.path.join(OUT, n)) for n in os.listdir(OUT))
    print(f"{ok} written · {bad} unresolved · data/px now {size/1e6:.1f} MB"
          + (f" · {off} sessions outside the calendar" if off else ""))


if __name__ == "__main__":
    main()
