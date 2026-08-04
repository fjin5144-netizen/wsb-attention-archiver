# Agent task briefs

Work queued for a coding agent with a large token budget. Read `../README.md` first —
it indexes six traps that were each a real bug.

## Ground rules

**Branch, never `main`.** GitHub Pages deploys from `main` on every push, so a bad
commit is live immediately. Work on `agent/<task-slug>`, push the branch, open a PR.

**`data/` is read-only.** It is the entire asset. 66 of its days were backfilled from
a source that has since 404'd and exist nowhere else. No script may delete, rewrite or
reformat anything under `data/apewisdom/`. Additive files are fine.

**Never force-push anything.**

**Verify by running, not by reading.** Every bug found in this project was found by
looking at rendered output that disagreed with the raw data — never by reading code.
A claim that something works needs a command whose output shows it working.

---

## T1 — Reconstruct attention history from Reddit  *(highest value)*

**Why.** Every conclusion this project has is provisional for one reason: the archive
is ~2 months long, so nothing can be stratified across market regimes. The previous
study's hardest lesson was that a pooled p=0.000 result flipped sign across
consecutive half-years. More code cannot fix this. Only more history can.

**What.** r/wallstreetbets submissions and comments are retrievable. Count ticker
mentions per day to rebuild a series structurally identical to ApeWisdom's, going back
years. Output `data/reddit/YYYY-MM-DD.json` in the same shape as `data/apewisdom/`.

**The trap that sinks naive implementations.** Many common English words are valid
tickers: `A`, `IT`, `ALL`, `ON`, `GO`, `SO`, `ARE`, `NOW`, `OPEN`, `REAL`, `CAR`,
`EAT`, `HAS`, `NEW`, `ONE`, `OUT`, `PLAY`, `TRUE`, `WELL`. Regex-matching uppercase
tokens makes those explode and produces a series that is mostly noise.

**Acceptance criterion — this is the whole task.** The 95 days we already hold are the
answer key. Reconstruct that same window and compare per-ticker, per-day against
`data/apewisdom/`. Report Spearman correlation of daily ranks, and overlap of the
daily top-20. **If the overlap period does not reproduce, the historical series is
worthless and must not be used.** State the number; do not describe it qualitatively.

Only after the overlap validates should the series be extended backwards.

---

## T2 — Threshold robustness grid

**Why.** The finding rests on `mentions ≥ 30`, `≥ 3× trailing-20d median`, 20-day
horizon. Those three constants were chosen, not derived. If the result only exists in
that corner of the parameter space, it is an artifact.

**What.** Re-run the event study across floor ∈ {10, 20, 30, 50, 100} × multiple ∈
{2, 3, 5, 10} × horizon ∈ {5, 10, 20, 40}, each with the matched-placebo bootstrap
already implemented in `~/Downloads/wsb-research/v2-real-mentions/`. 320 cells.

**Report as a surface, not a table of p-values.** The question is not "which cells are
significant" — with 320 cells some will be by chance. It is whether the effect varies
smoothly with the thresholds (a real phenomenon) or appears only in isolated cells
(an artifact). Say which.

Carry the existing discipline: median and trimmed mean, not mean; placebo baseline,
not zero; and flag any cell whose sign is unstable across sub-periods.

---

## T3 — Precompute the event set in CI

**Why.** The browser currently fetches ~96 archive files on every visit to Aftermath
and recomputes every spike client-side. That is slow, and it is where bug #1 lived —
a caching mistake in that recomputation silently produced 231 events instead of 238.
Computing it once, in CI, where it can be asserted against, removes the whole class.

**What.** A build step that emits `data/events.json`: every confirmed spike with its
date, ticker, sector, mentions, trailing median, multiple, and forward returns. The
page loads that one file. Keep the client-side path as a fallback if the file is
absent.

**Acceptance.** The event count and every per-event field must match the current
client-side computation exactly before the fallback is considered. Prove it with a
diff, not an assertion that it looks right.

---

## T4 — Validation that fails loudly

**Why.** The dangerous failure in this project has never been a crash. It has been
silence: an empty object cached as if it were data; a price pack drifting 17 days
behind; a snapshot that could commit successfully while containing nothing. The
workflow now alerts on failure, but degradation does not fail.

**What.** A validator run in CI after every snapshot that exits non-zero on:

- schema drift (missing `filters.wallstreetbets`, missing keys on a row)
- an implausible payload (row count far from 500, all-zero mentions, no rank 1)
- a payload byte-identical to the previous day — the API silently serving a cache
- prices more than 3 days behind the newest archive day
- gaps, duplicates or out-of-order dates across the whole archive

Plus a `--audit` mode that runs the same checks over all existing days and reports.
Run it once over the current archive and report what it finds; a validator that has
never been run against real history has not been tested.

---

## T5 — Regression tests with golden values

**Why.** Bug #1 would have been caught instantly by one test: "the event count the
site computes equals the count Python computes from the same files." Nothing like that
exists.

**What.**
- Python tests over `scripts/` covering spike detection, the price refresher's
  universe derivation, and its keep-stale-on-failure policy.
- One headless-browser test that loads the site, waits for the Lab, reads the event
  count out of the DOM, and asserts it equals the Python ground truth.
- Golden values checked in, with a documented way to update them when a threshold
  legitimately changes.

---

## T6 — Off-site archive backup

**Why.** `data/apewisdom/` is the project. The `backup` branch lives in the same repo,
under the same account — it protects against a bad commit, not against losing the
account. 66 of those days cannot be re-fetched from anywhere.

**What.** A scheduled job that mirrors `data/` somewhere with a different failure
domain (a second remote, or object storage). Include a restore procedure and prove it
by actually restoring into a scratch directory and diffing.

---

## Not for an agent

Front-end and visual design work is handled separately. Do not restyle the site, and
do not "simplify" the guards described in the README — particularly the `mapOf()`
memoisation check, which looks redundant and is not.
