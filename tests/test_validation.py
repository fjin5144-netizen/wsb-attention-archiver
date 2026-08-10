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


def test_the_cross_period_check_is_reproducible():
    """finding_rank.json is the one artefact that says the headline does NOT generalise,
    which makes it exactly the artefact nobody would notice going wrong. Recompute it.
    """
    path = os.path.join(ROOT, "data", "finding_rank.json")
    if not os.path.exists(path):
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "finding_rank", os.path.join(ROOT, "scripts", "finding_rank.py"))
    fr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fr)
    with open(path) as f:
        shipped = json.load(f)
    assert shipped == fr.compute(), \
        "data/finding_rank.json is stale — rerun scripts/finding_rank.py"
    # The sparse dataset is the whole point of it; losing it silently would leave a
    # cross-period claim resting on one quarter again.
    labels = [d["label"] for d in shipped["datasets"]]
    assert any("wayback" in l for l in labels), f"the 2021-2026 arm vanished: {labels}"


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


def test_the_browser_scan_starts_from_the_same_definition_as_the_precompute():
    """app.html now recomputes the spike set client-side so the definition can be moved,
    and the whole feature rests on the two implementations agreeing at the default. They
    are verified to agree on the numbers by selfCheck() in the browser and by an
    out-of-band diff against this Python at eight settings, but neither of those runs in
    CI — and the cheapest way to break the agreement is to edit one constant here and
    forget the other. So: the four numbers, asserted equal across the two files.

    Lookback is not a named constant on the Python side; it is the literal 20 in both
    `range(20, len(dates))` and `dates[i-20:i]`, which is exactly why it is worth
    pinning — an unnamed number is the one nobody remembers to change twice.
    """
    import re
    app = os.path.join(ROOT, "app.html")
    if not os.path.exists(app):
        return
    with open(app) as f:
        m = re.search(r"const SPK0=\{floor:(\d+),mult:([\d.]+),look:(\d+),gap:(\d+)\}", f.read())
    assert m, "could not read SPK0 out of app.html"
    floor, mult, look, gap = int(m.group(1)), float(m.group(2)), int(m.group(3)), int(m.group(4))

    src = os.path.join(ROOT, "scripts", "precompute_events.py")
    with open(src) as f:
        py = f.read()
    def const(name):
        c = re.search(rf"^{name}\s*=\s*(\d+)", py, re.M)
        assert c, f"could not read {name} out of precompute_events.py"
        return int(c.group(1))

    lookbacks = set(re.findall(r"range\((\d+), len\(dates\)\)", py)) \
              | set(re.findall(r"dates\[i - (\d+):i\]", py))
    assert lookbacks == {str(look)}, (
        f"app.html scans a {look}-day lookback; precompute_events.py uses {sorted(lookbacks)}")
    assert (floor, mult, gap) == (const("HOT_FLOOR"), float(const("HOT_X")), const("GAP")), (
        f"default spike definition differs: app.html {(floor, mult, gap)} vs "
        f"precompute_events.py {(const('HOT_FLOOR'), const('HOT_X'), const('GAP'))}")


def test_the_browser_scan_reproduces_the_python_away_from_the_default():
    """scripts/crosscheck_scan.{py,js} run app.html's own scan against this Python at 40
    definitions. The workflow runs them as their own step; this makes them reachable from
    `pytest` too, because a check nobody runs locally is a check that breaks on a Friday.

    Skipped rather than failed without node: the archive job must never go red over a
    missing local toolchain, and CI has node.
    """
    import shutil, subprocess, tempfile
    if not shutil.which("node"):
        import pytest
        pytest.skip("node not installed")
    with tempfile.TemporaryDirectory() as tmp:
        ref = os.path.join(tmp, "scan_ref.json")
        for cmd in (["python3", os.path.join(ROOT, "scripts", "crosscheck_scan.py"), ref],
                    ["node", os.path.join(ROOT, "scripts", "crosscheck_scan.js"), ref]):
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
            assert r.returncode == 0, f"{os.path.basename(cmd[1])} failed:\n{r.stdout}\n{r.stderr}"


def test_price_shards_align_with_the_research_pack():
    """data/px/ is a display-only price history for every ticker the archive has seen —
    the research pack covers only the ~155 that spike, so 70% of the board opened to a
    chart with no line. Two things can go wrong silently.

    The first is the shared calendar. Every shard stores `i`, an index into
    data/px/_dates.json, so a date inserted into the middle of that file would move every
    shard's closes to the wrong days without changing a single shard. Sorted and unique
    is the invariant that makes `i` mean anything.

    The second is the numbers. Where a ticker exists in both, the shard and
    prices_hist.json must agree — they are the same source read by two scripts, and two
    readers of one endpoint that quietly disagree is how half a pack ends up wrong while
    looking fine.
    """
    px = os.path.join(ROOT, "data", "px")
    dates_file = os.path.join(px, "_dates.json")
    if not os.path.exists(dates_file):
        return
    with open(dates_file) as f:
        dates = json.load(f)
    assert dates == sorted(set(dates)), "data/px/_dates.json must be sorted and unique"

    with open(os.path.join(ROOT, "data", "prices_hist.json")) as f:
        hist = json.load(f)

    checked = 0
    disagree = {}
    for name in sorted(os.listdir(px)):
        if name.startswith("_") or not name.endswith(".json"):
            continue
        tk = name[:-5]
        with open(os.path.join(px, name)) as f:
            sh = json.load(f)
        assert sh["i"] >= 0 and sh["i"] + len(sh["c"]) <= len(dates), (
            f"{tk} runs off the end of the calendar: i={sh['i']} len={len(sh['c'])}")
        if tk not in hist:
            continue
        old = dict(zip(hist[tk]["d"], hist[tk]["c"]))
        for k, v in enumerate(sh["c"]):
            if v is None:
                continue
            d = dates[sh["i"] + k]
            if d in old and old[d]:
                checked += 1
                if abs(v - old[d]) > 0.011:
                    disagree.setdefault(tk, []).append(round(old[d] / v, 4))

    # Not "they must be equal". They were, until the shards started being refreshed, and
    # then IBM's 61 pre-2021-11-03 closes moved by a constant factor of 0.956 — the
    # Kyndryl spinoff, re-based in one series and not the other. That is a corporate
    # action, not a bug, and it has a signature: every disagreeing close for the ticker
    # shifts by the *same* ratio. A parsing fault or a mixed-up field would not.
    #
    # So the ratio is what gets asserted. A ticker whose disagreements scatter is a real
    # defect and still fails; one that moves as a block is a re-basing and is allowed,
    # with the tickers named so a new one is noticed rather than absorbed.
    scattered = {tk: sorted(set(r))[:5] for tk, r in disagree.items()
                 if len(set(r)) > 1 or not r}
    assert not scattered, (
        f"closes disagree between data/px and prices_hist.json by inconsistent ratios, "
        f"which no corporate action explains: {scattered}")
    assert checked > 50_000, f"only {checked} closes compared — the check is not running"
    assert set(disagree) <= {"IBM"}, (
        f"a new ticker was re-based between the two price series: "
        f"{ {tk: (len(r), r[0]) for tk, r in disagree.items()} }")

    # _ends.json is what stops the daily job chasing delisted tickers forever: a name that
    # stopped trading is permanently behind the calendar, so it sorts to the front of the
    # stale queue every run, gets refetched, does not move, and crowds out the shards that
    # would actually gain a session. The entry is only meaningful if it names the date the
    # shard really ends on — a stale entry would let a live ticker go unrefreshed instead.
    ends_file = os.path.join(px, "_ends.json")
    if os.path.exists(ends_file):
        with open(ends_file) as f:
            ends = json.load(f)
        for tk, claimed in ends.items():
            path = os.path.join(px, f"{tk}.json")
            if not os.path.exists(path):
                continue
            with open(path) as f:
                sh = json.load(f)
            real = next((dates[sh["i"] + k] for k in range(len(sh["c"]) - 1, -1, -1)
                         if sh["c"][k] is not None), None)
            assert real == claimed, (
                f"_ends says {tk} ends {claimed} but its shard ends {real}")
