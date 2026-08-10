#!/usr/bin/env python3
"""Rebuild ApeWisdom history from the Wayback Machine -> data/wayback/{date}.json

The original backfill source (Samdd-oui/apewisdom-tracker) is 404 and ApeWisdom
itself serves only today. But archive.org crawled the HTML board — 611 distinct days
between 2021-05-10 and 2025-11-10, against the 99 the daily archive holds. The API
endpoint was never crawled; the page was, and the page carries the same numbers.

**This does not go in data/apewisdom/, and the difference is not cosmetic.**

A confirmed spike is `mentions >= 30 and >= 3x the median of the prior 20 archive
days`. That definition silently assumes the prior 20 archive days are the prior 20
calendar days. Wayback coverage is roughly a third of days, so "the prior 20 archive
days" there spans about two months — the same arithmetic measuring a different thing.
Merging the two would produce one events.json in which a spike means one thing for
some rows and something else for others, and nothing would error. That is the same
mixed-clock mistake that put calendar days under a session-counted return and a
rolling 24h window under a UTC date key.

So this writes a parallel dataset. Same schema, so the same code can read it; separate
directory, so no analysis can mix them without saying so.

Two more things it is not:

  * not contiguous with the archive — Wayback stops 2025-11-10, the archive starts
    2026-05-01, and nothing covers the gap between
  * not read at a consistent hour — crawl times are whenever archive.org felt like it,
    and `mentions` is a rolling 24h count, so `fetched_at` matters as much here as it
    does in the daily archive. It is recorded per day and left for the analysis to
    filter on

Depth is fine: the crawls hold the top ~100, and across the last 30 days of the real
archive every ticker at or above the 30-mention floor ranked #26 or better.

    python3 scripts/backfill_wayback.py --survey     # what exists, no fetching
    python3 scripts/backfill_wayback.py --limit 20   # fetch 20 missing days
    python3 scripts/backfill_wayback.py             # fetch everything missing
"""
import gzip, html, json, os, re, sys, time, urllib.parse, urllib.request, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "wayback")
CDX = "http://web.archive.org/cdx/search/cdx"
UA = "salience-research-backfill (github.com/fjin5144-netizen/wsb-attention-archiver)"
PAUSE = 2.5          # archive.org is a nonprofit and it 503'd me at four requests
RETRIES = 4
TARGET_HOUR = 22     # prefer a crawl after the US close, to match the daily archive
MAX_TRIES = 4        # fall through to the next crawl of the same day when one is unusable


def decode(raw):
    """`id_` replay hands back the bytes exactly as archived, original Content-Encoding
    and all, with no header saying so. Roughly 40% of the crawls were stored gzipped,
    and those arrived as binary that parsed to an empty board without erroring —
    5 of the first 12 days were being dropped as "layout not recognised" while the
    layout was fine and the bytes were simply still compressed."""
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    elif raw[:1] == b"\x78":
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
    return raw.decode("utf-8", "replace")


def get(url, timeout=90):
    """Fetch with backoff. The CDX endpoint 503s readily and recovers on its own."""
    last = None
    for i in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return decode(r.read())
        except Exception as e:
            last = e
            time.sleep(PAUSE * (2 ** i))
    raise RuntimeError(f"gave up on {url[:90]}: {last}")


def survey():
    """Every crawl of the board, as {date: [(timestamp, url), ...]}.

    The URL pattern is deliberately narrow, and the obvious widening is a trap worth
    documenting because it looks like free data.

    `apewisdom.io/` — the bare homepage — is archived on 264 days against this pattern's
    196, and 108 of those are days no other source covers. It parses cleanly: same
    columns (#, Name, Symbol, Mentions, 24h, Trend, Upvotes), same 100 rows, same
    /stocks/<TK>/ links, no crypto anywhere. Adding it here would have grown the crawl
    archive by about half, and nothing in the parser or the validator would have
    objected.

    It is not this board. On the 128 days where both were crawled, only 11-20% of the
    tickers they share carry the same mention count, and the two disagree on rank order.
    The decisive pair is 2021-06-08, crawled five seconds apart:

        homepage 15:40:51   BB 4199  CLOV 4061  GME 2309  AMC 2144
        r/wsb    15:40:56   BB 4309  CLOV 4284  GME 2196  AMC 1890

    A rolling 24-hour count does not move by 110 in five seconds, and the differences do
    not run one way — BB is lower on the homepage, GME higher — so it is not a superset
    of this board either. It is a different aggregation wearing the same table.

    So the homepage stays out. This is the same failure the crawl backfill already hit
    twice, in two other costumes: a gzipped `id_` response that parsed to an empty board
    without erroring, and a Mentions column that carries the class `th-votes`. Data that
    looks right and is not is the expensive kind here, because the whole archive is
    numbers nobody can check against anything else.
    """
    q = urllib.parse.urlencode({
        "url": "apewisdom.io/wallstreetbets*", "output": "json",
        "fl": "timestamp,original,statuscode", "filter": "statuscode:200",
    })
    rows = json.loads(get(f"{CDX}?{q}"))[1:]
    days = {}
    for ts, original, _ in rows:
        days.setdefault(f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}", []).append((ts, original))
    return days


def ranked(snaps):
    """The day's crawls, best first.

    A day read at 07:00 and a day read at 22:00 are 24h windows over different spans,
    so prefer the one whose window matches what the daily archive aims for. Returned
    as an ordered list rather than a single pick because a crawl can be a redirect, a
    truncated capture or an error page — 2025-03-20 had six and the closest to the
    close was the one that would not parse.
    """
    return sorted(snaps, key=lambda s: abs(int(s[0][8:10]) - TARGET_HOUR))


def parse(page):
    """Board rows out of the archived HTML.

    Mapped by header text, never by class: the column labelled Mentions carries
    `th-votes` and the one labelled Upvotes carries `th-mentions`.
    """
    heads = [html.unescape(re.sub("<[^>]+>", "", h)).strip()
             for h in re.findall(r"<th[^>]*>(.*?)</th>", page, re.S)]
    body = re.search(r"<tbody[^>]*>(.*?)</tbody>", page, re.S)
    if not body or "Symbol" not in heads or "Mentions" not in heads:
        return []
    i_sym, i_men = heads.index("Symbol"), heads.index("Mentions")
    i_rank = heads.index("#") if "#" in heads else None
    i_up = heads.index("Upvotes") if "Upvotes" in heads else None
    i_name = heads.index("Name") if "Name" in heads else None

    def txt(c):
        return html.unescape(re.sub("<[^>]+>", " ", c)).strip()

    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1), re.S):
        c = [txt(x) for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if len(c) <= max(i_sym, i_men):
            continue
        sym, men = c[i_sym], c[i_men].replace(",", "")
        if not re.fullmatch(r"[A-Z.\-]{1,6}", sym) or not men.isdigit():
            continue
        row = {"rank": len(out) + 1, "ticker": sym, "mentions": int(men)}
        if i_rank is not None and c[i_rank].isdigit():
            row["rank"] = int(c[i_rank])
        if i_name is not None:
            row["name"] = c[i_name]
        if i_up is not None:
            u = c[i_up].replace(",", "")
            if u.isdigit():
                row["upvotes"] = int(u)
        out.append(row)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    have = {n[:-5] for n in os.listdir(OUT) if n.endswith(".json")}
    print("surveying the Wayback index…", flush=True)
    days = survey()

    by_year = {}
    for d in days:
        by_year[d[:4]] = by_year.get(d[:4], 0) + 1
    print(f"{len(days)} crawled days, {min(days)} → {max(days)}")
    for y in sorted(by_year):
        print(f"  {y}: {by_year[y]:3d} days ({by_year[y] / 365 * 100:.0f}% of the year)")
    print(f"already fetched: {len(have)}")

    todo = sorted(d for d in days if d not in have)
    if "--survey" in sys.argv:
        print(f"would fetch {len(todo)} days · ~{len(todo) * PAUSE / 60:.0f} min at {PAUSE}s apiece")
        return

    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
        todo = todo[:limit]

    print(f"fetching {len(todo)} days\n")
    ok = empty = failed = 0
    for n, day in enumerate(todo, 1):
        rows, ts, url, err = [], None, None, None
        for cand_ts, original in ranked(days[day])[:MAX_TRIES]:
            # `id_` returns the original bytes without the Wayback toolbar injected.
            cand_url = f"http://web.archive.org/web/{cand_ts}id_/{original}"
            try:
                rows = parse(get(cand_url))
            except Exception as e:
                err = e
                rows = []
            if rows:
                ts, url = cand_ts, cand_url
                break
            time.sleep(PAUSE)
        if not rows:
            if err:
                print(f"  {day}  FAILED  {err}")
                failed += 1
            else:
                print(f"  {day}  empty (no usable crawl among {len(days[day])})")
                empty += 1
            time.sleep(PAUSE)
            continue
        with open(os.path.join(OUT, f"{day}.json"), "w") as f:
            json.dump({
                "fetched_at": f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}:{ts[12:14]}Z",
                "source": "wayback", "snapshot": url,
                "filters": {"wallstreetbets": rows},
            }, f)
        ok += 1
        if n % 10 == 0 or n == len(todo):
            print(f"  {n}/{len(todo)}  ok={ok} empty={empty} failed={failed}  last={day} "
                  f"({len(rows)} rows, top {rows[0]['ticker']}={rows[0]['mentions']})", flush=True)
        time.sleep(PAUSE)

    print(f"\ndone · {ok} written, {empty} unparseable, {failed} failed -> data/wayback/")


if __name__ == "__main__":
    main()
