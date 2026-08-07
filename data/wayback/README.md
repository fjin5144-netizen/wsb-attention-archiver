# data/wayback — a parallel dataset, not part of the archive

Rebuilt from archive.org's crawls of the ApeWisdom board by
`scripts/backfill_wayback.py`. 230 days, 2021-05-10 → 2026-07-18, top ~100 per day.
The original backfill source (`Samdd-oui/apewisdom-tracker`) is 404 and ApeWisdom
itself serves only today, so this is the only route to anything before 2026-05-01.

## Do not merge it into data/apewisdom

A confirmed spike is `mentions >= 30 and >= 3x the median of the prior 20 archive
days`. That silently assumes the prior 20 archive days *are* the prior 20 calendar
days. Here they are not:

    2021:  16 days       2024:  24 days
    2022:  15 days       2025: 119 days
    2023:  12 days       2026:  42 days

At 3–33% coverage, "the prior 20 archive days" spans about two months. The same
arithmetic would be measuring a different thing, in the same events.json, without
erroring — the mixed-clock mistake this repo has now made twice.

`tests/test_validation.py::test_the_wayback_backfill_stays_out_of_the_archive` fails
if these files ever reach `data/apewisdom/` or `data/days.json`.

## What it is good for

A five-year read of the same metric across market states the daily archive will never
see — the 2021 meme peak (`BB=1409, AMC=1194, GME=746`), the 2022 bear, 2025. Useful
as a coarse cross-period comparison with its own definitions, stated as such.

## What it is not

* **Not contiguous with the archive.** Nothing covers 2025-11 → 2026-05.
* **Not read at a consistent hour.** Crawl times are archive.org's, not ours; 61% land
  after the US close. `mentions` is a rolling 24h count, so `fetched_at` decides what a
  row means. It is recorded per day — filter on it.
* **Not daily.** See the coverage table.

## Is it real?

Yes, and it was checked rather than assumed. Mention peaks land on documented events
with the right tickers, dates and magnitudes:

    CLOV  2021-06-07    266 mentions, close $11.92
          2021-06-08  4,284 mentions, close $22.15    the Clover Health squeeze, +86%
    AMC   2021-06-08  1,890 mentions, $485            the June 2021 AMC peak
    BB    2021-06-08  4,309 mentions                  the same meme wave
    GME   2022-03-23  2,371 mentions, 33.5% 5d range  the RC Ventures disclosure week

Nothing garbled reproduces four independent, checkable events on the right days. Also:
230/230 days have ranks ascending and mentions descending, so no column slipped, and
92.4% of parsed tickers already appear in the archive or the price pack — the rest are
2021-era names (WISH, SNDL, CLOV, BITF) that simply stopped trending before 2026.

## But 11 days are not usable

ApeWisdom occasionally served a frozen page. Five consecutive days in September 2025
are byte-identical despite five separate crawls at five different hours, and
2023-03-21 matches 2023-05-17 to 98.4% *two months apart*. Adjacent days normally
agree on 3.6% of counts.

`scripts/verify_wayback.py` records them in `quality.json` — flagged, not deleted,
because the captures are what archive.org holds and rewriting them would lose the
evidence. **219 of 230 days are usable.** Read `quality.json` and skip the rest.

## Verified against the archive

Two days overlap, and they say the same thing when read at the same time:

    2026-05-12   API 22:41 · crawl 22:37   0.1h apart   top-20 20/20   mentions differ 0.0%
    2026-07-18   API 14:42 · crawl 22:11   7.5h apart   top-20 16/20   mentions differ 73.2%

Two independent pipelines — HTML page versus JSON API — agreeing to the digit when the
clock agrees. The second row is the read-time drift this project keeps rediscovering,
measured a third way.

## Schema

Same shape as `data/apewisdom/`, plus provenance:

    {"fetched_at": "...Z",          // the real crawl time, UTC
     "source": "wayback",
     "snapshot": "http://web.archive.org/web/…",
     "filters": {"wallstreetbets": [{"rank", "ticker", "mentions", "name", "upvotes"}]}}
