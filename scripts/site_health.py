#!/usr/bin/env python3
"""Probe the LIVE site — the one layer nothing else watches.

Every defect that actually reached a reader this month lived in a place no existing
check could see. The unit tests cover the Python; the E2E test drives a browser against
a LOCAL server; the workflows check their own steps. None of them touch the deployed
site, and that is exactly where three bugs surfaced:

  * Jekyll silently dropped data/px/_dates.json — files present in git, 404 in
    production, chart gone from 370 rows. Local serving cannot reproduce it.
  * NaN in a browser guard badged 1,639 clean tickers as suspect. No Python test can
    see JavaScript arithmetic.
  * A cancelled Pages build sat in the Actions list looking like a failure when it was
    GitHub superseding one build with a newer one — the opposite problem: red that
    means nothing, which trains people to ignore red.

So this asks the production site the questions a reader's browser would:

  reachable   — every file the app fetches answers 200 at a sane size, underscore
                paths included, which doubles as the .nojekyll guard in production
  fresh       — the newest archived day is recent; a site that stops updating looks
                exactly like a healthy one until someone counts the days
  coherent    — the derived files agree with each other's invariants: the worklist is
                computed and small, the artefact ledger is intact, a price shard
                actually decodes against the calendar it indexes into
  parseable   — the page's script passes a syntax check, so a truncated or corrupted
                deploy is caught even when every asset individually serves

Exit codes follow watchdog.py: 0 healthy, 1 found problems (the caller reports them;
the run itself stays green so red keeps meaning "the check is broken"), 2 the probe
itself failed. A wd-fingerprint line dedups the alert issue.

    python3 scripts/site_health.py
    python3 scripts/site_health.py --site http://localhost:8777
"""
import datetime as dt, hashlib, json, re, subprocess, sys, time, urllib.request

SITE = "https://fjin5144-netizen.github.io/wsb-attention-archiver"

# Path, minimum plausible size. Sizes are floors well under normal, not targets: the
# failure mode is a 404 page or an empty file, not a file 10% smaller than yesterday.
FILES = [
    ("app.html", 100_000), ("data/days.json", 500), ("data/events.json", 3_000),
    ("data/finding.json", 300), ("data/finding_rank.json", 300),
    ("data/prices.json", 50_000), ("data/artifacts.json", 5_000),
    ("data/divergence.json", 5_000), ("data/divergence_daily.json", 100_000),
    ("data/gap_series.json", 50_000), ("data/wayback/quality.json", 2_000),
    ("data/px/_dates.json", 5_000), ("data/px/_missing.json", 100),
    ("data/px/_ends.json", 500), ("data/px/AAPL.json", 2_000),
]

# Strings whose absence means a specific regression shipped. Each earned its place.
MARKERS = [
    ("flagBadge", "artefact badges"),
    ("NOSTK", "non-stock rows out of the ordering"),
    ("dv.diverging", "worklist read from the file, not re-derived — the NaN bug"),
    ("needGap", "the 75 uncovered days"),
    ("rankOdds", "rank resampling"),
]


def fetch(base, path, tries=3):
    """Three tries with a pause, because the very first live run of this probe reported
    gap_series.json as a 503 that was gone two seconds later — a CDN blip, not the site.
    A monitor that files an issue for another machine's hiccup is the cry-wolf alarm
    this whole file exists to avoid; a real outage fails all three times just fine."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(f"{base}/{path}",
                                         headers={"User-Agent": "site-health"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def run(base):
    bad = []

    blobs = {}
    for path, floor in FILES:
        try:
            b = blobs[path] = fetch(base, path)
            if len(b) < floor:
                bad.append(f"{path}: {len(b)} bytes, expected at least {floor}")
        except Exception as e:
            bad.append(f"{path}: {getattr(e, 'code', e)}")

    if "data/days.json" in blobs:
        days = json.loads(blobs["data/days.json"])
        age = (dt.date.today() - dt.date.fromisoformat(max(days))).days
        if age > 2:
            bad.append(f"newest archived day {max(days)} is {age} days old — collection "
                       f"has stopped and nothing on the page says so")

    if "data/divergence.json" in blobs:
        d = json.loads(blobs["data/divergence.json"])
        if "diverging" not in d:
            bad.append("divergence.json has no `diverging` — consumers will re-derive "
                       "the worklist, which is the NaN bug's door")
        elif len(d["diverging"]) > max(40, len(d.get("tickers", {})) * 0.05):
            bad.append(f"worklist has {len(d['diverging'])} entries — a guard has failed")

    if "data/artifacts.json" in blobs:
        a = json.loads(blobs["data/artifacts.json"])
        if len(a.get("confirmed", {})) < 20:
            bad.append(f"artifacts.json carries {len(a.get('confirmed', {}))} confirmed "
                       f"artefacts, below the 20 already established — the ledger shrank")

    if "data/px/_dates.json" in blobs and "data/px/AAPL.json" in blobs:
        dates = json.loads(blobs["data/px/_dates.json"])
        sh = json.loads(blobs["data/px/AAPL.json"])
        if dates != sorted(set(dates)):
            bad.append("_dates.json is not a sorted unique calendar")
        elif not (0 <= sh["i"] and sh["i"] + len(sh["c"]) <= len(dates)):
            bad.append("AAPL shard does not decode against the live calendar — "
                       "shards and calendar deployed out of step")

    if "app.html" in blobs:
        page = blobs["app.html"].decode("utf-8", "replace")
        for needle, what in MARKERS:
            if needle not in page:
                bad.append(f"app.html lost `{needle}` ({what})")
        scripts = re.findall(r"<script>([\s\S]*?)</script>", page)
        if scripts:
            try:
                subprocess.run(["node", "--check", "/dev/stdin"],
                               input=max(scripts, key=len).encode(),
                               capture_output=True, timeout=30, check=True)
            except FileNotFoundError:
                pass          # no node here — the check is extra, not the point
            except subprocess.CalledProcessError as e:
                bad.append(f"app.html script fails to parse: "
                           f"{e.stderr.decode()[:200]} — a corrupt or truncated deploy")

    return bad


def main():
    base = (sys.argv[sys.argv.index("--site") + 1] if "--site" in sys.argv else SITE).rstrip("/")
    try:
        bad = run(base)
    except Exception as e:                       # the probe itself broke, not the site
        print(f"probe failed: {e}")
        sys.exit(2)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    if bad:
        fp = hashlib.sha1("\n".join(sorted(bad)).encode()).hexdigest()[:12]
        print(f"### Site problem · {stamp}\n")
        for b in bad:
            print(f"- {b}")
        print(f"\nwd-fingerprint:{fp}")
        sys.exit(1)
    print(f"### Site healthy · {stamp}\n\n- {len(FILES)} files served at sane sizes"
          f"\n- newest day fresh, invariants hold, page parses")
    sys.exit(0)


if __name__ == "__main__":
    main()
