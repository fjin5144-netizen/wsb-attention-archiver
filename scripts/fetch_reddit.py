#!/usr/bin/env python3
"""Raw r/wallstreetbets posts and comments from Arctic Shift -> data/reddit/

Everything this project knows about attention comes from one tracker reading one board.
ApeWisdom decides what a mention is, which tickers resolve, and when the count is taken,
and none of it can be checked from the outside — the archive records what it said, not
what was said. It also has a 75-day hole (2026-02-15..04-30) that archive.org cannot
fill, because nothing crawled the board then.

Arctic Shift carries the underlying posts and comments themselves, 2021 to within the
hour. That is a different kind of source: with the raw text, a mention count can be
computed under a rule we state rather than accepted from one we cannot see.

**It is not the same series and must never be merged into one.** ApeWisdom's tokeniser,
its ticker universe (Infinite Marketcap) and its 24h window are its own, and matching them
exactly is not achievable — the point is a second measurement to compare against, not a
patch for the first. Anything derived here lands under its own name.

What the API can and cannot do, measured rather than assumed:

  * limit caps at 100, so a day of r/wallstreetbets (~25,000 comments) is ~250 requests.
    A bounded slice is fine. Five years is ~458,000 requests and is not; that is what the
    monthly dumps and the Academic Torrents mirror are for.
  * Paging is a cursor on created_utc in seconds, and successive pages do not overlap —
    checked on a 200-comment sample, zero duplicate ids.
  * The full-text `body=` filter exists but times out under repeated use, so this pulls
    everything in the window and filters locally.

Fields are trimmed to what a count needs. The full records average 1,936 bytes; id, time,
text and score come to about a fourteenth of that.

    python3 scripts/fetch_reddit.py 2026-08-10                 # one day
    python3 scripts/fetch_reddit.py 2026-02-15 2026-04-30      # a range, resumable
    python3 scripts/fetch_reddit.py --status
"""
import gzip, json, os, subprocess, sys, time, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "reddit")
API = "https://arctic-shift.photon-reddit.com/api"
SUB = "wallstreetbets"
PAGE = 100          # the API's ceiling
PAUSE = 0.6         # unmetered and free; this is the whole of what it asks in return
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def get(url, tries=5):
    """One request, with backoff. 422 here means 'slow down', not 'bad request'."""
    for i in range(tries):
        r = subprocess.run(["curl", "-s", "-L", "--max-time", "90",
                            "-H", f"User-Agent: {UA}", url],
                           capture_output=True, text=True)
        try:
            j = json.loads(r.stdout)
        except Exception:
            time.sleep(2 * (i + 1))
            continue
        if isinstance(j.get("data"), list):
            return j["data"]
        # "Timeout. Maybe slow down a bit" — the server asking, so ask less often.
        time.sleep(4 * (i + 1))
    return None


def trim(kind, x):
    """Only what a mention count needs, plus the score so weighting stays possible."""
    out = {"id": x.get("id"), "t": x.get("created_utc"), "s": x.get("score")}
    if kind == "comments":
        out["b"] = x.get("body") or ""
    else:
        out["b"] = ((x.get("title") or "") + "\n" + (x.get("selftext") or "")).strip()
    return out


def fetch_day(day, kind):
    """Every item of `kind` created on `day`, paging on created_utc.

    The cursor is exclusive on the server side — two consecutive pages shared zero ids in
    testing — so a page shorter than the limit means the window is exhausted.
    """
    start = int(dt.datetime.strptime(day, "%Y-%m-%d")
                  .replace(tzinfo=dt.timezone.utc).timestamp())
    end = start + 86400
    rows, cursor, seen = [], start, set()
    while True:
        d = get(f"{API}/{kind}/search?subreddit={SUB}&after={cursor}&before={end}"
                f"&limit={PAGE}&sort=asc")
        if d is None:
            return None, "the API stopped answering"
        new = [x for x in d if x.get("id") not in seen]
        for x in new:
            seen.add(x["id"])
            rows.append(trim(kind, x))
        if len(d) < PAGE:
            break
        nxt = max(x["created_utc"] for x in d)
        if nxt <= cursor:            # no forward progress: stop rather than spin
            break
        cursor = nxt
        time.sleep(PAUSE)
    return rows, None


def path_for(day, kind):
    return os.path.join(OUT, day[:7], f"{day}-{kind}.jsonl.gz")


def have(day, kind):
    p = path_for(day, kind)
    return os.path.exists(p) and os.path.getsize(p) > 0


def write(day, kind, rows):
    p = path_for(day, kind)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n")
    return os.path.getsize(p)


def days_between(a, b):
    d0 = dt.date.fromisoformat(a)
    d1 = dt.date.fromisoformat(b)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def status():
    if not os.path.isdir(OUT):
        print("data/reddit is empty")
        return
    days, size = set(), 0
    for dirpath, _, files in os.walk(OUT):
        for n in files:
            if n.endswith(".jsonl.gz"):
                days.add(n[:10])
                size += os.path.getsize(os.path.join(dirpath, n))
    if not days:
        print("data/reddit is empty")
        return
    d = sorted(days)
    both = sum(1 for x in d if have(x, "comments") and have(x, "posts"))
    print(f"{len(d)} days {d[0]}..{d[-1]} · {both} complete · {size/1e6:.1f} MB")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--status" in sys.argv:
        status()
        return
    if not args:
        sys.exit(__doc__.strip().splitlines()[-3].strip())
    days = days_between(args[0], args[1]) if len(args) > 1 else [args[0]]

    os.makedirs(OUT, exist_ok=True)
    done = skipped = 0
    for day in days:
        line = []
        for kind in ("comments", "posts"):
            if have(day, kind):
                skipped += 1
                line.append(f"{kind} kept")
                continue
            rows, err = fetch_day(day, kind)
            if err:
                # Leave the day unwritten rather than half-written: `have()` is how a
                # rerun knows what to skip, and a partial file would be skipped as done.
                print(f"{day} {kind}: {err} — stopping so it can be resumed", flush=True)
                return
            kb = write(day, kind, rows) / 1024
            line.append(f"{kind} {len(rows)} ({kb:.0f} KB)")
            done += 1
        print(f"{day}  " + " · ".join(line), flush=True)
    print(f"\n{done} files written, {skipped} already present")
    status()


if __name__ == "__main__":
    main()
