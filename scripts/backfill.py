#!/usr/bin/env python3
"""One-off backfill: import historical daily snapshots from the public
Samdd-oui/apewisdom-tracker archive (data starts 2026-05-01) into this repo's
per-day schema. Skips any date that already has a file, so this repo's own
canonical snapshots always win and re-runs only fill holes.
"""
import json, os, urllib.request

SRC = "https://raw.githubusercontent.com/Samdd-oui/apewisdom-tracker/main/data/history.json"

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = f"{root}/data/apewisdom"
    os.makedirs(out, exist_ok=True)
    with urllib.request.urlopen(SRC, timeout=60) as r:
        hist = json.load(r)
    byday = {}
    for snap in hist:                       # later snapshots overwrite -> last of day wins
        day = snap.get("ts", "")[:10]
        if len(day) == 10 and snap.get("filters"):
            byday[day] = snap
    added = skipped = 0
    for day, snap in sorted(byday.items()):
        p = f"{out}/{day}.json"
        if os.path.exists(p):
            skipped += 1
            continue
        json.dump({"fetched_at": snap["ts"], "filters": snap["filters"],
                   "source": "backfill:Samdd-oui/apewisdom-tracker"}, open(p, "w"))
        added += 1
    print(f"backfill: +{added} days, {skipped} already present, "
          f"range {min(byday)}..{max(byday)}")

if __name__ == "__main__":
    main()
