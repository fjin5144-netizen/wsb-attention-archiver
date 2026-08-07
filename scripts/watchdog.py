#!/usr/bin/env python3
"""Check that collection actually happened. Exit 1 with a report if it did not.

Why this exists rather than another alert step inside archive.yml: on 2026-08-06 two
scheduled runs sat 15 minutes each waiting for a hosted runner and were cancelled
with `runner=` empty and `steps=0`. No step ran, so no in-job alert could ever have
fired — and GitHub then dropped every remaining slot for six hours, so 2026-08-06
froze at 15:29 UTC, before the close, silently.

That failure is invisible to anything that watches the job. It is obvious to
something that watches the data, which is what this does. It asks the four questions
whose answers the archive exists to keep true, and it asks them from the files rather
than from any workflow's exit code.

    python3 scripts/watchdog.py          # report, exit 1 if anything is wrong
"""
import hashlib, json, os, sys, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "data", "apewisdom")
CLOSE_UTC = 21          # clears the 16:00 ET close in both EDT and EST
PRICE_LAG_DAYS = 4      # a long weekend plus a holiday, and no more

# The scheduled slots in archive.yml, UTC. Staleness is measured against the most
# recent one that has passed rather than a flat number of hours: collection is idle
# from 23:05 to 11:45 by design, so any fixed threshold either false-alarms every
# morning or is too loose to notice a stall at noon. A first draft used 8 hours and
# would have cried wolf at the 09:35 check every single day.
SLOTS = [(11, 45)] + [(h, 15) for h in range(13, 21)] + [(21, 5), (21, 35), (22, 5), (22, 35), (23, 5)]
GRACE_H = 3.5           # GitHub routinely runs an hour or two late; that is not an outage


def last_due_slot(now):
    """Most recent scheduled run that should already have happened."""
    for back in (0, 1):
        d = now.date() - dt.timedelta(days=back)
        for h, m in sorted(SLOTS, reverse=True):
            t = dt.datetime.combine(d, dt.time(h, m), dt.timezone.utc)
            if t <= now:
                return t
    return None


def read_day(day):
    with open(os.path.join(ARCHIVE, f"{day}.json")) as f:
        j = json.load(f)
    ts = dt.datetime.fromisoformat(j["fetched_at"].replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    rows = (j.get("filters") or {}).get("wallstreetbets") or []
    return ts, rows


def main():
    now = dt.datetime.now(dt.timezone.utc)
    days = sorted(n[:-5] for n in os.listdir(ARCHIVE)
                  if n.endswith(".json") and n[:-5].count("-") == 2)
    problems, notes = [], []

    if not days:
        print("FAIL: the archive is empty")
        sys.exit(1)

    # 1. No date gaps. The archive's whole value is that it has none.
    first, last = dt.date.fromisoformat(days[0]), dt.date.fromisoformat(days[-1])
    expected = (last - first).days + 1
    if len(days) != expected:
        have = set(days)
        missing = [(first + dt.timedelta(days=i)).isoformat() for i in range(expected)
                   if (first + dt.timedelta(days=i)).isoformat() not in have]
        problems.append(("gap:" + ",".join(missing),
                         f"**{len(missing)} missing archive day(s)**: {', '.join(missing[:8])}"
                         + (" …" if len(missing) > 8 else "")))
    else:
        notes.append(f"{len(days)} days, {days[0]} → {days[-1]}, no gaps")

    # 2. Collection is still alive.
    ts, rows = read_day(days[-1])
    age = (now - ts).total_seconds() / 3600
    due = last_due_slot(now)
    if due and ts < due - dt.timedelta(hours=GRACE_H):
        problems.append((f"stale:{days[-1]}:{due:%Y-%m-%dT%H:%M}",
                         f"**No snapshot since {ts:%Y-%m-%d %H:%M} UTC** ({age:.1f}h ago). "
                         f"A run was due at {due:%Y-%m-%d %H:%M} UTC and nothing has landed since. "
                         f"Scheduled runs are being dropped or cancelled."))
    else:
        notes.append(f"newest read {days[-1]} {ts:%H:%M} UTC ({age:.1f}h ago), {len(rows)} tickers")

    # 3. The last completed day settled after the close. This is the one that caught
    #    2026-08-06: nothing failed loudly, the day just never got a late read.
    yesterday = (now.date() - dt.timedelta(days=1)).isoformat()
    if now.hour >= 1 and yesterday in days:
        yts, _ = read_day(yesterday)
        if yts.hour < CLOSE_UTC:
            problems.append((
                f"unsettled:{yesterday}",
                f"**{yesterday} froze at {yts:%H:%M} UTC, before the US close.** Mentions is a "
                f"rolling 24h count, so that day covers a different window than the days beside "
                f"it and is not comparable to them. The post-close slots did not run."))
        else:
            notes.append(f"{yesterday} settled at {yts:%H:%M} UTC, after the close")

    # 4. Prices still move, or outcomes quietly stop appearing.
    pf = os.path.join(ROOT, "data", "prices.json")
    if os.path.exists(pf):
        with open(pf) as f:
            px = json.load(f)
        ends = [v["d"][-1] for v in px.values() if v and v.get("d")]
        if ends:
            newest = max(ends)
            lag = (now.date() - dt.date.fromisoformat(newest)).days
            if lag > PRICE_LAG_DAYS:
                problems.append((f"prices:{newest}",
                                 f"**Prices are {lag} days behind** (newest bar {newest}). "
                                 f"Spikes will show no outcome until this catches up."))
            else:
                notes.append(f"prices through {newest} ({lag}d behind today)")

    stamp = f"{now:%Y-%m-%dT%H:%MZ}"
    if problems:
        # A frozen day cannot be un-frozen, so re-reporting it three times a day is
        # pure noise. The fingerprint is over what is wrong, not when it was noticed,
        # so the workflow can tell "still the same thing" from "something new".
        fp = hashlib.sha1("|".join(sorted(k for k, _ in problems)).encode()).hexdigest()[:12]
        print(f"### Collection problem · {stamp}\n")
        for _, p in problems:
            print(f"- {p}")
        print("\n<details><summary>Everything else that is fine</summary>\n")
        for n in notes:
            print(f"- {n}")
        print("\n</details>")
        print(f"\n<!-- wd-fingerprint:{fp} -->")
        sys.exit(1)

    print(f"### Collection healthy · {stamp}\n")
    for n in notes:
        print(f"- {n}")


if __name__ == "__main__":
    main()
