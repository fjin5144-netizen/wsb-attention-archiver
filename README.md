# Salience

Daily archive of retail attention on r/wallstreetbets, and what the price did next.

**Live:** https://fjin5144-netizen.github.io/wsb-attention-archiver/

Not investment advice, and deliberately not a trading tool — it never touches a
brokerage account. It exists to answer one question honestly: does attention predict
anything, and if so, over what horizon.

---

## What is here

```
index.html                       the whole site — markup, styles, script, price pack
data/apewisdom/YYYY-MM-DD.json   one snapshot per day, 500 tickers, from ApeWisdom
data/{spy,risk,risk_status}.json, data/DIX.csv    market backdrop series
scripts/archive.py               the daily collector (snapshots + backdrop)
scripts/refresh_prices.py        rebuilds the PRICES block inside index.html
scripts/backfill.py              one-off historical import (its source is now 404)
.github/workflows/archive.yml    the cron that runs all of it
```

The site has three views, split by **time** rather than by widget type:

| | |
|---|---|
| **Today** | the board on the selected archive day |
| **Aftermath** | every past spike and what the price did next |
| **Market** | systemic gauges, explicitly *not* about WSB attention |

---

## Things that will bite you

Each of these was a real bug or a real misreading. The code carries comments at the
relevant lines; this is the index.

### 1. `mapOf()` must not memoise an unresolved day

It used to be `MAPS[d] || (MAPS[d] = build(d))`. Asking for a day *before* its fetch
landed cached an empty object — and `{}` is truthy, so that day stayed empty for the
rest of the session however much data arrived later.

It zeroed mention history in the charts (MU read 0 after 2026-06-23 while the archive
held 2302 the next day) and fed zeros into spike detection: **231 events instead of
238**. Cache only once `d in CACHE`, which is true for a result *and* for a failure,
but false while the request is still in flight.

### 2. The price pack goes stale unless something refreshes it

`PRICES` lives inside `index.html`. Attention grows daily through the workflow; prices
do not, and they had drifted 17 days behind — mention bars ran to the right edge while
the price line stopped short, and most 20-day returns read `—` because the window
contained no prices at all.

`scripts/refresh_prices.py` now runs on the post-close schedule. It derives its ticker
universe from the archive (everything that ever crossed the confirmed tier, plus the
research basket), so a newly-spiking name needs no list edit. **A failed fetch keeps
the embedded history rather than dropping the ticker.**

### 3. Flat stretches in the timeline are weekends, not missing data

The archive has no missing days. Weekend WSB volume falls to about a third of a
weekday's, so the handful of names still clearing 30 mentions are perennials whose
trailing median is already high — they cannot also clear 3×. A Saturday *cannot*
produce an event. The strip renders three states so an empty column never reads the
same as an absent one.

### 4. The event chart window is asymmetric on purpose

Roughly `N/3` days before the spike, `N` after. What follows a burst is the thing
under study. The control used to be labelled "±15d", which is why a chart stopping
fifteen days past the spike read as data running out. Default is `1M` (+22d) because
every card quotes a 20-day return and the old +15d window could not show it.

### 5. Served over `http://localhost`, data comes from the checkout

`LOCAL` is `location.protocol === 'file:'`. Over localhost the date list still comes
from the GitHub API while the day files come from your working copy — so a stale clone
produces an empty-looking site with a full date dropdown. `git pull` first. The page
also falls back to the most recent day that has rows, and says so.

### 6. Attention counts are a rolling 24h window

Snapshot time therefore matters. Weekday captures cluster near 22:42 UTC (SD ~32 min,
no day more than 1.5 h off). Weekends drift by up to ±7 h because the hourly cron is
weekday-only — another reason weekend days are unusable for comparison.

---

## Running it

```bash
python3 scripts/archive.py                      # take a snapshot now
python3 scripts/refresh_prices.py               # rebuild prices (~3 min, 143 tickers)
python3 scripts/refresh_prices.py --dry-run     # report only
```

The site is a single static file. Open `index.html` directly (it falls back to
fetching data from GitHub raw) or serve the repo root over HTTP.

View state lives in the URL hash: `#aftermath?tk=MU&x=10&o=dn` restores the tab, the
focused ticker and every filter, so a filtered screen is a link.

---

## What the research actually found

Analysis lives outside this repo (`~/Downloads/wsb-research/`) and reads these JSON
files directly with Python — it never goes through the site's JavaScript, which is why
bug #1 never touched the conclusions.

- **The volume proxy was broken.** 68% of real attention spikes have no volume spike;
  log-ratio correlation 0.59. Earlier work found nothing using volume as a stand-in
  for attention — the proxy was the problem, not the hypothesis.
- **Attention spike → 20-day underperformance.** Median −12.7% against a matched
  placebo, 22% win rate; survives median, trimmed mean, calendar-day cluster bootstrap
  and dropping the two most crowded event days.
- **The 5-day version is not real.** It was those two crowded days: p goes 0.000 →
  0.418 once they are removed.
- **The window is ~2 months, so none of it can be stratified across regimes** — and
  the hardest lesson from the earlier basket study was that a p=0.000 pooled result
  flipped sign across consecutive half-years. Read every number here as *worth another
  look in six months*, not as a finding.

The archive is the asset. 66 of its days were backfilled from a source that has since
404'd and exist nowhere else. What this project needs most is not more code — it is
uninterrupted collection.
