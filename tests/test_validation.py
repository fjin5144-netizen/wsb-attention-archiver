"""Golden-value tests.

The bug these exist for: a caching mistake once made the site compute 231 events
instead of 238, and a precompute script later produced 170 instead of 239. Neither
crashed. Both looked plausible. The only thing that catches that class is an
independent recomputation compared against what shipped.

Every assertion here recomputes from data/apewisdom/ rather than trusting an
artefact, because an artefact compared against itself always agrees.
"""
import json, os, statistics, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import validate  # noqa: E402

HOT_FLOOR, HOT_X, GAP = 30, 3, 5


def archive_events():
    """Recompute the confirmed-spike set straight from the snapshots.

    Deliberately a second implementation rather than an import of
    precompute_events.compute — a golden test that calls the code under test only
    checks that the function is deterministic.
    """
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
    universe = set()
    for k in maps:
        universe |= set(maps[k])
    out = set()
    for tk in universe:
        last = -99
        for i in range(20, len(dates)):
            m = maps[dates[i]].get(tk, 0)
            if m < HOT_FLOOR:
                continue
            med = statistics.median([maps[p].get(tk, 0) for p in dates[i - 20:i]])
            if m >= HOT_X * max(med, 1) and (i - last) > GAP:
                out.add((dates[i], tk))
                last = i
    return out


def test_events_json_matches_the_archive():
    with open(os.path.join(ROOT, "data", "events.json")) as f:
        shipped = {(e["d"], e["tk"]) for e in json.load(f)}
    truth = archive_events()
    missing, extra = truth - shipped, shipped - truth
    assert not missing, f"{len(missing)} events missing from events.json, e.g. {sorted(missing)[:5]}"
    assert not extra, f"{len(extra)} events in events.json the archive does not support"


def test_basket_members_are_not_excluded():
    """The precompute once skipped basket tickers, dropping 69 of 239 events."""
    import re
    with open(os.path.join(ROOT, "index.html")) as f:
        m = re.search(r"const BASKET=new Set\(\[([^\]]*)\]", f.read())
    basket = set(re.findall(r"[A-Z]{1,5}", m.group(1)))
    assert basket, "could not read BASKET out of index.html"
    # SPY is the benchmark and QQQ an index ETF — neither belongs to the research
    # basket. An earlier test asserted they did, and failed for that reason.
    assert "SPY" not in basket and "QQQ" not in basket
    with open(os.path.join(ROOT, "data", "events.json")) as f:
        shipped = json.load(f)
    assert {e["tk"] for e in shipped} & basket, \
        "no basket ticker appears in events.json — they are being excluded again"


def test_validator_accepts_real_days_and_rejects_corruption(tmp_path):
    d = os.path.join(ROOT, "data", "apewisdom")
    real = sorted(n for n in os.listdir(d) if n.endswith(".json"))
    assert validate.validate_day(os.path.join(d, real[-1])), "validator rejects a real snapshot"

    def write(name, payload):
        p = tmp_path / name
        p.write_text(json.dumps(payload))
        return str(p)

    good = {"ticker": "MU", "mentions": 500, "rank": 1}
    cases = {
        "schema-drift": {"filters": {}},
        "too-few-rows": {"filters": {"wallstreetbets": [good] * 10}},
        "no-rank-1": {"filters": {"wallstreetbets":
                      [{"ticker": "MU", "mentions": 5, "rank": 2}] * 500}},
        "all-zero": {"filters": {"wallstreetbets":
                     [{"ticker": "MU", "mentions": 0, "rank": i + 1} for i in range(500)]}},
        "missing-keys": {"filters": {"wallstreetbets": [{"ticker": "MU"}] * 500}},
    }
    for label, payload in cases.items():
        assert not validate.validate_day(write(f"{label}.json", payload)), \
            f"validator accepted a payload it should reject: {label}"


def test_price_universe_covers_every_event_ticker():
    """Prices are what turn a spike into an outcome; an event ticker with no price
    series shows '—' forever."""
    with open(os.path.join(ROOT, "data", "prices.json")) as f:
        prices = set(json.load(f))
    missing = {tk for _, tk in archive_events()} - prices
    assert not missing, f"{len(missing)} event tickers have no price series: {sorted(missing)[:10]}"
