#!/usr/bin/env python3
"""Reference results for the browser's spike scan, at many definitions -> a JSON file.

app.html reimplements precompute_events.py and finding.py in JavaScript so the spike
definition can be moved with sliders. selfCheck() in the browser proves the port at the
*default* definition, against data/events.json and data/finding.json — but the default
is exactly the one setting where a golden file exists. Everywhere else, the port could
drift and nothing would say so, which matters because the whole point of the control is
that people will leave the default.

So: this writes what the Python produces at a spread of settings, and crosscheck_scan.js
checks the JavaScript against it. That pair found a real divergence the golden files
could not — Python's round() breaks ties to even and Math.round breaks them upward, so a
win rate of 2 out of 16 came out 12 here and 13 there. Nothing at the default reaches an
exact tie.

The settings cover the slider's own range and its eight corners, plus a seeded random
spread, so a rerun compares the same points.

    python3 scripts/crosscheck_scan.py /tmp/scan_ref.json          # 40 settings
    python3 scripts/crosscheck_scan.py /tmp/scan_ref.json --n 120  # slower, wider
"""
import json, os, random, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import finding as F                                    # noqa: E402  reuse, never re-derive

# The slider's own stops, so nothing here is unreachable through the UI.
FLOORS = range(5, 205, 5)
MULTS = [1.5 + 0.5 * i for i in range(14)]
LOOKS = (5, 40)
GAPS = (0, 20)
CORNERS = [(30, 3, 20, 5),                             # the shipped definition
           (5, 1.5, 5, 0), (200, 8, 40, 20),           # loosest / tightest
           (5, 8, 40, 0), (200, 1.5, 5, 20),
           (5, 1.5, 40, 20), (200, 8, 5, 0),
           (185, 3.5, 40, 5)]                          # the banker's-rounding case


def load_archive():
    d = os.path.join(ROOT, "data", "apewisdom")
    dates, maps = [], {}
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(d, name)) as f:
            snap = json.load(f)
        rows = (snap.get("filters") or {}).get("wallstreetbets") or []
        if not rows:
            continue
        dates.append(name[:-5])
        maps[name[:-5]] = {r["ticker"]: r["mentions"] for r in rows}
    return dates, maps


def scan(dates, maps, universe, floor, mult, look, gap):
    """precompute_events.compute, with the four constants as arguments.

    Deliberately a copy rather than an import: that module's constants are module-level
    by design — they are what the shipped artefact is built from — and making them
    parameters there would let a caller change what the pipeline produces.
    """
    events = []
    for tk in sorted(universe):
        last = -99
        for i in range(look, len(dates)):
            m = maps[dates[i]].get(tk, 0)
            med = st.median([maps[p].get(tk, 0) for p in dates[i - look:i]])
            if m >= floor and m >= mult * max(med, 1) and (i - last) > gap:
                events.append({"tk": tk, "d": dates[i], "m": m, "med": med})
                last = i
    events.sort(key=lambda x: x["d"], reverse=True)
    return events


def main():
    out_path = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    if not out_path:
        sys.exit(__doc__.strip().splitlines()[-3].strip())
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 40

    dates, maps = load_archive()
    universe = set()
    for d in dates:
        universe |= set(maps[d])
    with open(os.path.join(ROOT, "data", "prices.json")) as f:
        prices = json.load(f)
    with open(os.path.join(ROOT, "data", "days.json")) as f:
        days = json.load(f)

    random.seed(11)            # seeded: a rerun must compare the same settings
    cfgs, seen = list(CORNERS[:n]), set(CORNERS[:n])
    while len(cfgs) < n:
        c = (random.choice(FLOORS), random.choice(MULTS),
             random.randint(*LOOKS), random.randint(*GAPS))
        if c not in seen:
            seen.add(c)
            cfgs.append(c)

    out = {}
    for c in cfgs:
        events = scan(dates, maps, universe, *c)
        h = next((x for x in F.compute(events, prices, days)["horizons"]
                  if x["sessions"] == 20), None)
        out["-".join(map(str, c))] = {
            "n": len(events),
            "keys": sorted(e["tk"] + "|" + e["d"] for e in events),
            "spike": h["spike"] if h else None,
            "placebo": h["placebo"] if h else None,
            "gap_median": h["gap_median"] if h else None,
        }

    with open(out_path, "w") as f:
        json.dump(out, f)
    sizes = [v["n"] for v in out.values()]
    print(f"{len(out)} definitions · {min(sizes)}..{max(sizes)} events -> {out_path}")


if __name__ == "__main__":
    main()
