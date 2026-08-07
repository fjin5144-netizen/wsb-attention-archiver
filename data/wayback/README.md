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
