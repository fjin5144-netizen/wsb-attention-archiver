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


def test_the_wayback_backfill_stays_out_of_the_archive():
    """data/wayback holds ~230 sparse days rebuilt from archive.org. Its coverage runs
    3–33% of the calendar, so "the prior 20 archive days" there spans about two months
    and the spike definition means something else. Merged in, one events.json would
    hold two definitions and nothing would error — so assert the separation instead of
    trusting it.
    """
    wb = os.path.join(ROOT, "data", "wayback")
    if not os.path.isdir(wb):
        return
    theirs = {n[:-5] for n in os.listdir(wb) if n.endswith(".json")}
    arch = os.path.join(ROOT, "data", "apewisdom")
    ours = {n[:-5] for n in os.listdir(arch) if n.endswith(".json")}
    with open(os.path.join(ROOT, "data", "days.json")) as f:
        listed = set(json.load(f))

    strays = sorted(d for d in theirs - ours if d in listed)
    assert not strays, f"wayback days reached days.json: {strays[:5]}"
    for d in sorted(theirs & ours):
        with open(os.path.join(arch, f"{d}.json")) as f:
            assert json.load(f).get("source") != "wayback", \
                f"data/apewisdom/{d}.json came from the backfill, not from collection"


def test_the_wayback_quality_manifest_is_current():
    """ApeWisdom sometimes served a frozen page: five days in September 2025 are
    byte-identical across five separate crawls, and 2023-03-21 matches 2023-05-17 to
    98.4% two months apart. Against a 3.6% median similarity between adjacent days
    those are not subtle — they were just invisible until something looked. This
    fails if the manifest drifts from what the files actually say.
    """
    wb = os.path.join(ROOT, "data", "wayback")
    manifest = os.path.join(wb, "quality.json")
    if not os.path.isdir(wb) or not os.path.exists(manifest):
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "verify_wayback", os.path.join(ROOT, "scripts", "verify_wayback.py"))
    vw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vw)
    days, snaps = vw.load()
    fresh = vw.compute(days, snaps)
    with open(manifest) as f:
        shipped = json.load(f)
    assert shipped == fresh, "data/wayback/quality.json is stale — rerun scripts/verify_wayback.py"
    # The flagging only means something while most days pass it.
    assert fresh["usable"] / fresh["days"] > 0.8, \
        f"only {fresh['usable']}/{fresh['days']} usable — the source, not the filter, is the problem"


def test_the_two_front_ends_agree_on_the_basket():
    """app.html carries its own copy of BASKET. Two copies of a list is how this repo
    has been bitten before — the precompute and the client-side scan once held the
    same wrong exclusion and agreed perfectly. Editing one and not the other would
    silently give the two front ends different research baskets.
    """
    import re
    app = os.path.join(ROOT, "app.html")
    if not os.path.exists(app):
        return
    def basket(path):
        with open(path) as f:
            m = re.search(r"BASKET=new Set\(\[([^\]]*)\]", f.read())
        assert m, f"could not read BASKET out of {os.path.basename(path)}"
        return re.findall(r"[A-Z]{1,5}", m.group(1))
    a, b = basket(os.path.join(ROOT, "index.html")), basket(app)
    assert a == b, ("index.html and app.html disagree on the research basket: "
                    f"only in index {sorted(set(a) - set(b))}, only in app {sorted(set(b) - set(a))}")


def test_finding_matches_an_independent_recomputation():
    """The headline claim is now an artefact, so it can be wrong the same silent way
    events.json was. Recomputed here from prices and events rather than by calling
    finding.compute — a golden test that calls the code under test only proves the
    function is deterministic, which is exactly what the seeded placebo already is.
    """
    import hashlib
    with open(os.path.join(ROOT, "data", "finding.json")) as f:
        shipped = json.load(f)
    with open(os.path.join(ROOT, "data", "events.json")) as f:
        events = json.load(f)
    with open(os.path.join(ROOT, "data", "prices.json")) as f:
        prices = json.load(f)
    with open(os.path.join(ROOT, "data", "days.json")) as f:
        days = json.load(f)

    def fwd(tk, day, n):
        p = prices.get(tk)
        if not p:
            return None
        d = p["d"]
        i = d.index(day) if day in d else next((k for k, x in enumerate(d) if x > day), -1)
        if i < 0 or i + n >= len(p["c"]) or not p["c"][i]:
            return None
        return (p["c"][i + n] / p["c"][i] - 1) * 100

    ev_days = {}
    for e in events:
        ev_days.setdefault(e["tk"], set()).add(e["d"])

    assert shipped["events"] == len(events)
    assert shipped["archive_days"] == len(days)
    for row in shipped["horizons"]:
        n = row["sessions"]
        real, plac = [], []
        for e in events:
            r = fwd(e["tk"], e["d"], n)
            if r is not None:
                real.append(r)
            pool = [d for d in days if d not in ev_days[e["tk"]]]
            if not pool:
                continue
            h = int(hashlib.sha256(f"{e['tk']}|{e['d']}|{n}".encode()).hexdigest(), 16)
            r = fwd(e["tk"], pool[h % len(pool)], n)
            if r is not None:
                plac.append(r)
        assert row["spike"]["n"] == len(real), f"{n}d: spike sample size drifted"
        assert row["placebo"]["n"] == len(plac), f"{n}d: placebo sample size drifted"
        assert row["spike"]["median"] == round(statistics.median(real), 2), f"{n}d median"
        assert row["placebo"]["median"] == round(statistics.median(plac), 2), f"{n}d placebo median"
        # The rule the project keeps re-learning: never report the mean alone.
        assert "trimmed_mean" in row["spike"] and "mean" in row["spike"]


def test_days_index_matches_the_archive_directory():
    """The page discovers its days from data/days.json. A stale one silently shortens
    the client-side scan — which is the one path that exists to catch a bad
    precompute, so it must not be the path that quietly goes blind first.
    """
    d = os.path.join(ROOT, "data", "apewisdom")
    on_disk = sorted(n[:-5] for n in os.listdir(d)
                     if n.endswith(".json") and n[:-5].count("-") == 2)
    with open(os.path.join(ROOT, "data", "days.json")) as f:
        listed = json.load(f)
    assert listed == on_disk, (
        f"days.json lists {len(listed)} days, the archive holds {len(on_disk)}; "
        f"missing {sorted(set(on_disk) - set(listed))[:5]}, "
        f"phantom {sorted(set(listed) - set(on_disk))[:5]}")


def test_price_universe_covers_every_event_ticker():
    """Prices are what turn a spike into an outcome; an event ticker with no price
    series shows '—' forever.

    Tickers no source will price are exempt, but only via the explicit list
    refresh_prices.py writes — a silent skip would make a broken fetcher look like a
    quiet day, and no exemption at all lets one dead symbol red-line the archive
    forever. The cap is what keeps the exemption from becoming a dumping ground.
    """
    with open(os.path.join(ROOT, "data", "prices.json")) as f:
        prices = set(json.load(f))
    gaps = set()
    gaps_file = os.path.join(ROOT, "data", "price_gaps.json")
    if os.path.exists(gaps_file):
        with open(gaps_file) as f:
            gaps = set(json.load(f))
    assert not (gaps & prices), \
        f"price_gaps.json claims tickers that are in fact priced: {sorted(gaps & prices)}"
    assert len(gaps) <= 8, \
        f"{len(gaps)} tickers exempted from pricing — that is a broken fetcher, not dead symbols"
    missing = {tk for _, tk in archive_events()} - prices - gaps
    assert not missing, f"{len(missing)} event tickers have no price series: {sorted(missing)[:10]}"
