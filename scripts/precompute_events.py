#!/usr/bin/env python3
"""Precompute the confirmed-spike set -> data/events.json.

Must reproduce index.html's client-side scan exactly. Two things got this wrong the
first time, both producing a plausible-looking file that is quietly short:

  * The universe is every ticker seen in ANY snapshot, taken from the archive — not
    the keys of prices.json. Prices are derived from the spike set, so sourcing the
    universe from them is circular and drops anything without a price entry.

  * Research-basket members are NOT excluded. They are the tickers most worth
    watching, they carry a badge in the UI, and skipping them silently removed 69 of
    239 events — every one of them a basket name.

Events carry only what the scan produces: ticker, date, mentions, trailing median.
Forward returns stay client-side because the page already loads prices.json and
enriches from it; computing them here too would duplicate the logic and let the two
drift.

    python3 scripts/precompute_events.py
    python3 scripts/precompute_events.py --check   # compare against the file, exit 1 on drift
"""
import json, os, statistics, sys

HOT_FLOOR = 30      # must match index.html
HOT_X = 3
GAP = 5             # dedupe window, in archive days


def load_archive(archive_dir):
    dates, maps = [], {}
    for name in sorted(os.listdir(archive_dir)):
        if not name.endswith(".json"):
            continue
        d = name[:-5]
        with open(os.path.join(archive_dir, name)) as f:
            snap = json.load(f)
        rows = (snap.get("filters") or {}).get("wallstreetbets") or []
        if not rows:
            continue
        dates.append(d)
        maps[d] = {r["ticker"]: r["mentions"] for r in rows}
    return dates, maps


def compute(dates, maps):
    universe = set()
    for d in dates:
        universe |= set(maps[d])
    events = []
    for tk in sorted(universe):
        last = -99
        for i in range(20, len(dates)):
            m = maps[dates[i]].get(tk, 0)
            med = statistics.median([maps[p].get(tk, 0) for p in dates[i - 20:i]])
            if m >= HOT_FLOOR and m >= HOT_X * max(med, 1) and (i - last) > GAP:
                events.append({"tk": tk, "d": dates[i], "m": m, "med": med})
                last = i
    events.sort(key=lambda x: x["d"], reverse=True)   # matches the JS localeCompare sort
    return events


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    archive_dir = os.path.join(root, "data", "apewisdom")
    out_file = os.path.join(root, "data", "events.json")

    if not os.path.isdir(archive_dir):
        sys.exit(f"archive not found: {archive_dir}")

    dates, maps = load_archive(archive_dir)
    if not dates:
        sys.exit("archive has no usable snapshots")
    events = compute(dates, maps)

    if "--check" in sys.argv:
        if not os.path.exists(out_file):
            sys.exit("data/events.json missing")
        with open(out_file) as f:
            have = json.load(f)
        a = {(e["tk"], e["d"]) for e in have}
        b = {(e["tk"], e["d"]) for e in events}
        if a == b and len(have) == len(events):
            print(f"events.json matches the archive: {len(events)} events")
            return
        print(f"DRIFT: file has {len(have)}, archive yields {len(events)}")
        for label, s in (("missing from file", b - a), ("stale in file", a - b)):
            if s:
                print(f"  {label}: {len(s)} · {sorted(s)[:8]}")
        sys.exit(1)

    with open(out_file, "w") as f:
        json.dump(events, f, separators=(",", ":"))
    print(f"{len(events)} events over {len(dates)} archive days "
          f"({dates[0]} .. {dates[-1]}) -> data/events.json")


if __name__ == "__main__":
    main()
