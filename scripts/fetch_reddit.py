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
    python3 scripts/fetch_reddit.py --status 2026-02-15 2026-04-30   # progress + ETA
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
    rows, cursor, seen, pages = [], start, set(), 0
    while True:
        d = get(f"{API}/{kind}/search?subreddit={SUB}&after={cursor}&before={end}"
                f"&limit={PAGE}&sort=asc")
        if d is None:
            return None, "the API stopped answering"
        new = [x for x in d if x.get("id") not in seen]
        for x in new:
            seen.add(x["id"])
            rows.append(trim(kind, x))
        pages += 1
        # Progress from inside the day, not only when it finishes. A day of comments is
        # ~260 requests and seven minutes, and printing nothing until the file lands
        # makes a working download indistinguishable from a hung one — which is exactly
        # how it looked from outside.
        if pages % 20 == 0:
            at = dt.datetime.fromtimestamp(cursor, dt.timezone.utc).strftime("%H:%M")
            print(f"    {day} {kind}: {len(rows):>6} so far, at {at} UTC", flush=True)
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


def status(target=None):
    """What is on disk, and — if a range is named — what is left and roughly how long.

    A download that takes hours needs somewhere to look. The first version of this
    printed a single span and a total, which said nothing about progress because the
    calibration days and the gap sit at opposite ends of the same range: "3 days
    2026-02-15..2026-08-11" is true and useless.
    """
    if not os.path.isdir(OUT):
        print("data/reddit is empty")
        return
    days, size, newest = {}, 0, []
    for dirpath, _, files in os.walk(OUT):
        for n in files:
            if not n.endswith(".jsonl.gz"):
                continue
            fp = os.path.join(dirpath, n)
            size += os.path.getsize(fp)
            days.setdefault(n[:10], []).append(os.path.getmtime(fp))
    done = sorted(d for d in days if have(d, "comments") and have(d, "posts"))
    if not done:
        print("data/reddit is empty")
        return

    # Contiguous runs read better than a min..max that spans a hole.
    runs, run = [], [done[0]]
    for a_, b_ in zip(done, done[1:]):
        if (dt.date.fromisoformat(b_) - dt.date.fromisoformat(a_)).days == 1:
            run.append(b_)
        else:
            runs.append(run); run = [b_]
    runs.append(run)
    for r in runs:
        print(f"  {r[0]}..{r[-1]}  {len(r)} day{'s' if len(r) > 1 else ''}")
    print(f"  {len(done)} days complete · {size/1e6:.1f} MB")

    if not target:
        return
    want = days_between(*target)
    left = [d for d in want if d not in set(done)]
    if not left:
        print(f"\n{target[0]}..{target[1]} is complete")
        return
    # Rate from the most recent finished days, which is what the next ones will cost.
    stamps = sorted(max(v) for d, v in days.items() if d in want and d not in left)
    per = None
    if len(stamps) > 2:
        per = (stamps[-1] - stamps[0]) / (len(stamps) - 1)
    eta = f" · about {per*len(left)/60:.0f} min left" if per else ""
    print(f"\n{target[0]}..{target[1]}: {len(want)-len(left)}/{len(want)} done, "
          f"{len(left)} to go{eta}")
    print(f"  next: {left[0]}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--status" in sys.argv:
        status(tuple(args[:2]) if len(args) > 1 else None)
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
            print(f"  {day} {kind} …", flush=True)
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
