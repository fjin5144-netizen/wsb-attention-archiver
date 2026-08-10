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

  * One file per ticker, and refreshing them daily is cheap — which is the opposite of
    what this comment said first. The estimate was "14 MB rewritten daily is 5 GB a
    year", arrived at by multiplying and not by measuring. Measured: seven days of
    rewriting 500 shards, 30.8 MB of raw writes, packs to 244 KiB. A shard only ever
    grows at the tail, which is the case git's delta compression is best at, so the
    honest figure is about 35 KiB a day for 500 tickers.

    So the constraint is the fetch, not the disk: --daily extends the calendar, fills in
    tickers new to the board, and refreshes the shards of tickers on the newest board
    that have fallen behind it. Off-board names keep whatever history they were written
    with, because nobody is looking at them and they are history either way.

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
    python3 scripts/shard_prices.py --daily         # what the workflow runs: extend the
                                                    # calendar, add new tickers, catch up
                                                    # the board's stale shards
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
    ends_path = os.path.join(OUT, "_ends.json")

    dates = load(dates_path, None)
    if not dates:
        cal = fetch(CAL)
        if not cal:
            sys.exit(f"cannot build the calendar: {CAL} did not resolve")
        dates = sorted(cal)
        with open(dates_path, "w") as f:
            json.dump(dates, f, separators=(",", ":"))
        print(f"calendar: {len(dates)} sessions {dates[0]}..{dates[-1]}")
    if "--daily" in sys.argv or "--extend-calendar" in sys.argv:
        # Appending is safe and inserting is not: every shard's `i` is an offset into this
        # list, so a date landing in the middle would silently move every close in every
        # file to the wrong day. Asserted rather than assumed.
        cal = fetch(CAL)
        if cal and dates:
            fresh = sorted(cal)
            # range=5Y is a rolling window: it gains a session at the tail and drops one
            # from the head, so the stored calendar is not a prefix of it and comparing
            # them as prefixes fails on the first run. (It did. The guard below is what
            # caught it.) The invariant that matters is narrower — where the two overlap
            # they must agree exactly, and growth is only ever appended, because `i` in
            # every shard is an offset from index 0.
            overlap = [d for d in fresh if d <= dates[-1]]
            mine = [d for d in dates if d >= fresh[0]]
            if overlap != mine:
                sys.exit(f"the calendar disagrees with the stored one over their shared "
                         f"range ({len(overlap)} vs {len(mine)} sessions) — refusing to "
                         f"touch _dates.json, because every shard's `i` indexes into it")
            added = [d for d in fresh if d > dates[-1]]
            if added:
                dates = dates + added
                with open(dates_path, "w") as f:
                    json.dump(dates, f, separators=(",", ":"))
                print(f"calendar +{len(added)}: {added[0]}..{added[-1]} ({len(dates)} total)")
    idx = {d: i for i, d in enumerate(dates)}

    missing = load(missing_path, {})
    # Tickers whose history genuinely stops before the calendar does. Without this the
    # daily job chases them forever: a delisted name is permanently "behind", so it sorts
    # to the front of the stale list every run, gets refetched, does not move, and crowds
    # out the shards that would actually gain a session. WFH stopped trading in 2021 and
    # was first in the queue.
    ends = load(ends_path, {})
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

    def newest_board():
        """Tickers on the most recent archived board — the ones anyone is going to click."""
        days = sorted(n for n in os.listdir(ARCHIVE)
                      if re.fullmatch(r"\d{4}-\d\d-\d\d\.json", n))
        if not days:
            return []
        with open(os.path.join(ARCHIVE, days[-1])) as f:
            snap = json.load(f)
        return [r["ticker"] for r in (snap.get("filters") or {}).get("wallstreetbets") or []]

    def last_date(tk):
        try:
            with open(os.path.join(OUT, f"{tk}.json")) as f:
                sh = json.load(f)
        except Exception:
            return None
        for k in range(len(sh["c"]) - 1, -1, -1):
            if sh["c"][k] is not None:
                return dates[sh["i"] + k] if sh["i"] + k < len(dates) else None
        return None

    def behind(tk):
        """Sessions a shard could still gain, or 0 if its history is known to end."""
        d = last_date(tk)
        if d is None or ends.get(tk) == d:
            return 0
        return sum(1 for x in dates if x > d)

    if "--daily" in sys.argv:
        # New tickers first — an empty chart is worse than a slightly stale one — then the
        # board's stalest shards with whatever budget is left.
        cap = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 600
        fresh_todo = [t for t in uni if t not in have and t not in missing][:min(cap, 100)]
        board = [t for t in newest_board() if t in have]
        stale = sorted(((behind(t) or 0, t) for t in board), reverse=True)
        todo = fresh_todo + [t for n, t in stale if n > 0][:max(0, cap - len(fresh_todo))]
        have -= set(todo)          # so the writer below does not skip the refreshes
        print(f"daily: {len(fresh_todo)} new · "
              f"{len(todo) - len(fresh_todo)} of {sum(1 for n, _ in stale if n > 0)} "
              f"stale board shards")
    elif "--refresh" in sys.argv:
        todo = [a.upper() for a in sys.argv[sys.argv.index("--refresh") + 1:]
                if not a.startswith("-")]
    elif "--retry-missing" not in sys.argv:
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
        # Freshly fetched and still short of the calendar means this is where its history
        # ends, not that the fetch failed. Recorded so the next run does not ask again.
        newest = dates[pos[-1][0]]
        if newest < dates[-1]:
            ends[tk] = newest
        else:
            ends.pop(tk, None)
        ok += 1
        if n % 100 == 0:
            print(f"  {n}/{len(todo)} · {ok} written · {bad} unresolved", flush=True)

    with open(missing_path, "w") as f:
        json.dump(missing, f, indent=0, sort_keys=True)
    with open(ends_path, "w") as f:
        json.dump(ends, f, indent=0, sort_keys=True)
    size = sum(os.path.getsize(os.path.join(OUT, n)) for n in os.listdir(OUT))
    print(f"{ok} written · {bad} unresolved · data/px now {size/1e6:.1f} MB"
          + (f" · {off} closes before the calendar begins, dropped" if off else ""))


if __name__ == "__main__":
    main()
