#!/usr/bin/env python3
"""Rank every board ticker by how far it sits from the stable offset between the two sources.

The instinct this serves is "use both sources, the information will be better". It will be,
but not by averaging them. ApeWisdom says DTE was mentioned 70 times and this project's rule
says 3; the mean of those, 36.5, is worse than either, because it is neither the count of the
options slang nor the count of the company, and it launders a row already proven false into
one that looks reasonable.

What two independent rules are actually good for is disagreement. Across 43 overlapping days
the ratio between them sits at 1.09 with a day-to-day sd of 0.066 — a stable instrument
offset. A ticker that sits far off that offset is not noise; it is a place where the two rules
disagree about *what counts as a mention of this company*, and every artefact recorded in
data/artifacts.json was found by looking exactly there:

    DTE  0.09   ApeWisdom counts 0DTE, the options slang        -> not a stock at all
    EU   0.00   the European Union                              -> 4 spike events, all false
    SELF 0.00   a bot's output tag, u/profanitycounter [SELF]    -> 1 spike event, false
    TASK 0.41   the phrase TASK FORCE                           -> 1 spike event, false
    YOU  3.71   caps-lock English, and OUR error, not theirs    -> we count 80 to their 15
    UP   3.69   same                                            -> ours again

So the output is a worklist, not a verdict. Nothing here says who is right — that only comes
from reading the text, which is what the `evidence` fields in data/artifacts.json record.

Counting is tokenised rather than one regex per ticker: 150 candidates against 30,000 items a
day is 190M regex calls and takes hours, while splitting each item into its words once and
intersecting sets takes minutes. --verify checks that shortcut against compare_counts.counter
on a sample, because a faster rule that quietly means something different is worse than a slow
one.

    python3 scripts/divergence.py
    python3 scripts/divergence.py --min-days 8 --depth 50
    python3 scripts/divergence.py --verify          # prove the tokeniser matches the regex
"""
import collections, datetime as dt, gzip, json, os, re, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REDDIT = os.path.join(ROOT, "data", "reddit")
ARCHIVE = os.path.join(ROOT, "data", "apewisdom")

sys.path.insert(0, os.path.join(ROOT, "scripts"))
from compare_counts import WORDLIKE, counter  # noqa: E402  one definition of the rule

WORD = re.compile(r"\$?[A-Za-z0-9]+")


def tokens(text):
    """The set of things in this item that could be a ticker mention.

    Two sets, matching compare_counts' two forms: bare uppercase words, and $-prefixed words
    case-folded. Splitting on [A-Za-z0-9]+ reproduces the regex's word boundaries — `0DTE`
    tokenises whole and so never matches DTE, exactly as the lookbehind intends.
    """
    bare, dollar = set(), set()
    for m in WORD.finditer(text):
        w = m.group(0)
        if w[0] == "$":
            dollar.add(w[1:].upper())
        else:
            bare.add(w)
    return bare, dollar


def hits(ticker, bare, dollar):
    if ticker in WORDLIKE:
        return ticker in dollar
    return ticker in bare or ticker in dollar


def verify(sample=400):
    """Hold the tokeniser against the regex it replaces, on real text."""
    day = sorted(n[:10] for _, _, fs in os.walk(REDDIT) for n in fs
                 if n.endswith("comments.jsonl.gz"))[-1]
    texts = []
    p = os.path.join(REDDIT, day[:7], f"{day}-comments.jsonl.gz")
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= sample:
                break
            texts.append(json.loads(line)["b"])
    tks = ["DTE", "NOW", "EU", "SELF", "TASK", "BE", "YOU", "UP", "IT", "OPEN",
           "NVDA", "TSLA", "SPY", "MU", "AAPL", "GME", "HTZ"]
    bad = 0
    for t in texts:
        b, d = tokens(t)
        for tk in tks:
            if hits(tk, b, d) != counter(tk)(t):
                bad += 1
                if bad <= 5:
                    print(f"  MISMATCH {tk}: {t[:90]!r}")
    print(f"{len(texts)} items x {len(tks)} tickers = {len(texts)*len(tks)} comparisons, "
          f"{bad} mismatch{'es' if bad != 1 else ''}")
    return bad == 0


def load_day(day, cache={}):
    if day not in cache:
        rows = []
        for kind in ("comments", "posts"):
            p = os.path.join(REDDIT, day[:7], f"{day}-{kind}.jsonl.gz")
            if os.path.exists(p):
                with gzip.open(p, "rt", encoding="utf-8") as f:
                    rows += [json.loads(l) for l in f]
        cache[day] = [(r["t"], *tokens(r["b"])) for r in rows]
        for k in [k for k in cache if k < (dt.date.fromisoformat(day)
                                           - dt.timedelta(days=2)).isoformat()]:
            del cache[k]
    return cache[day]


def main():
    a = sys.argv
    if "--verify" in a:
        sys.exit(0 if verify() else 1)
    depth = int(a[a.index("--depth") + 1]) if "--depth" in a else 500
    min_days = int(a[a.index("--min-days") + 1]) if "--min-days" in a else 6
    # Coverage and statistics are different jobs, and widening depth to 500 made that
    # obvious the hard way: every row is counted so it can say whether it was checked,
    # but a row ApeWisdom reports as 1 mention yields a ratio of 0, 1 or 2 and means
    # nothing. Including those dragged the common ratio from 1.13 to 1.00 and produced a
    # 285-name worklist of V, PM, T, F, DAY, BABY, HE, OR -- the deep board's ties, not
    # disagreements. So the ranking and the common offset take only days where the row
    # carried enough to be a measurement; the cache still records all of them.
    min_mentions = int(a[a.index("--min-mentions") + 1]) if "--min-mentions" in a else 10

    days = sorted(n[:-5] for n in os.listdir(ARCHIVE)
                  if re.fullmatch(r"\d{4}-\d\d-\d\d\.json", n))

    # Per-day results are kept so the corpus does not have to be. A day costs eight minutes
    # to fetch and a few seconds to count; what survives is one number per ticker. That is
    # what lets this run in CI, which fetches one day's text, counts it, commits the numbers
    # and throws the text away — 285 MB of raw posts never has to exist in the cloud, and
    # the history still accumulates. Locally it means a rerun re-counts nothing.
    cache_path = os.path.join(ROOT, "data", "divergence_daily.json")
    cache, dcache = {}, {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            j = json.load(f)
        cache = j.get("days", {})
        # Depth is part of what a cached day means. A day counted at the old top-50 holds
        # nothing for the 450 rows below it, and silently reusing it would leave the deep
        # board looking unchecked forever. Counting 500 costs the same as counting 50 —
        # the intersection is over each item's own tokens, not over the candidate list,
        # so the work is the tokenising, which is shared — and 50 was a guess that made
        # the newest arrivals, the ones a reader most wants checked, the least covered.
        if j.get("depth") != depth:
            cache = {}
        # The dollar counts are cached separately and were added later, so a day can have a
        # ratio and no dollar figure. Such a day is recounted while the corpus is still on
        # disk — after it is deleted the number is unrecoverable, and a half-populated test
        # would quietly under-report.
        dcache = j.get("dollar_days", {})
    cache = {d: v for d, v in cache.items() if d in dcache}
    fresh = 0

    # A second test, and the one that covers the first one's blind spot.
    #
    # Divergence finds tickers the two rules count differently. It cannot see the ones they
    # count the same way and are both wrong about: EU is the European Union, produced four
    # false spike events, and its ratio is 1.20 — dead normal, because both rules count the
    # bare uppercase word.
    #
    # This asks a different question, of the text alone rather than of the two counts: does
    # anyone ever write $TICKER? Measured over 21 days and 27 unambiguous tickers, not one
    # sits at zero — the lowest are ASTS at 1.3% and SPY at 1.5%. Meanwhile EU is 0 of 253,
    # JUST 0 of 437, DTE 0 of 126, SELF 0 of 43, TASK 0 of 29. The dollar sign is the one
    # thing a human writes to mean "the security", and a row nobody ever writes it for,
    # across hundreds of items, is not being written about as a security.
    #
    # It convicts in one direction only. Some $ usage does NOT mean the row is sound: BE
    # carries 24 $BE among 632 matches and is still nine-tenths caps-lock English. Between
    # them the two tests caught every artefact in data/artifacts.json; neither did alone.
    dollar = collections.defaultdict(lambda: [0, 0])

    ratios, seen_all, seen_days = {}, {}, 0
    for day in days:
        if day in cache:
            with open(os.path.join(ARCHIVE, f"{day}.json")) as f:
                ment = {r["ticker"]: r["mentions"]
                        for r in ((json.load(f).get("filters") or {}).get("wallstreetbets") or [])}
            for tk, r in cache[day].items():
                seen_all[tk] = seen_all.get(tk, 0) + 1
                if ment.get(tk, 0) >= min_mentions:
                    ratios.setdefault(tk, []).append(r)
            for tk, (nd, nm) in dcache.get(day, {}).items():
                dollar[tk][0] += nd
                dollar[tk][1] += nm
            seen_days += 1
            continue
        with open(os.path.join(ARCHIVE, f"{day}.json")) as f:
            snap = json.load(f)
        if not snap.get("fetched_at"):
            continue
        hi = int(dt.datetime.fromisoformat(
            snap["fetched_at"].replace("Z", "+00:00")).timestamp())
        lo = hi - 86400
        prev = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
        if not all(os.path.exists(os.path.join(REDDIT, d[:7], f"{d}-{k}.jsonl.gz"))
                   for d in (prev, day) for k in ("comments", "posts")):
            continue
        items = [x for x in load_day(prev) + load_day(day) if lo <= x[0] < hi]
        board = sorted((snap.get("filters") or {}).get("wallstreetbets") or [],
                       key=lambda r: -r["mentions"])[:depth]
        seen_days += 1
        fresh += 1
        today, dtoday = {}, {}
        for r in board:
            if r["mentions"] <= 0:
                continue
            n = sum(1 for _, b, d in items if hits(r["ticker"], b, d))
            today[r["ticker"]] = round(n / r["mentions"], 4)
            seen_all[r["ticker"]] = seen_all.get(r["ticker"], 0) + 1
            if r["mentions"] >= min_mentions:
                ratios.setdefault(r["ticker"], []).append(n / r["mentions"])
            # Second, independent test — see the note above `dollar` below.
            nd = sum(1 for _, _, d in items if r["ticker"] in d)
            dollar[r["ticker"]][0] += nd
            dollar[r["ticker"]][1] += max(n, nd)
            dtoday[r["ticker"]] = [nd, max(n, nd)]
        cache[day] = today
        dcache[day] = dtoday
        print(f"  {day} scanned", flush=True)

    if fresh:
        with open(cache_path, "w") as f:
            json.dump({"_note": ("Per-day, per-ticker ratio of this project's count to "
                                 "ApeWisdom's. Kept so the 285 MB of raw Reddit text does not "
                                 "have to be — see the comment in scripts/divergence.py."),
                       "depth": depth, "min_mentions": min_mentions, "days": dict(sorted(cache.items())),
                       "dollar_days": dict(sorted(dcache.items()))}, f, indent=0)
        print(f"  {fresh} new day(s) counted -> data/divergence_daily.json", flush=True)

    if not ratios:
        sys.exit("no day has both an archived board and the Reddit text for its window")

    common = st.median([st.median(v) for v in ratios.values() if len(v) >= min_days])
    rows = [(tk, st.median(v), len(v)) for tk, v in ratios.items() if len(v) >= min_days]
    rows.sort(key=lambda x: -abs(x[1] - common))

    known = {}
    ap = os.path.join(ROOT, "data", "artifacts.json")
    if os.path.exists(ap):
        with open(ap) as f:
            art = json.load(f)
        known = {**{k: "confirmed" for k in art.get("confirmed", {})},
                 **{k: "ours" for k in art.get("our_own_errors", {}) if k.isupper()},
                 **{k: "cleared" for k in art.get("cleared", {}) if k.isupper()}}

    print(f"\n{seen_days} days, top {depth}, tickers seen on {min_days}+ days")
    print(f"common ratio {common:.2f} — the stable offset between the two rules\n")
    print(f"{'ticker':<8}{'ratio':>7}{'days':>6}   status")
    for tk, r, n in rows[:25]:
        note = {"confirmed": "artefact — see artifacts.json",
                "ours": "OUR error — see artifacts.json",
                "cleared": "checked, real"}.get(known.get(tk), "")
        if not note and abs(r - common) > 0.5:
            note = "<- unexplained, worth reading the text"
        print(f"{tk:<8}{r:>7.2f}{n:>6}   {note}")
    NEVER_DOLLAR_MIN = 20      # below this the zero says nothing; ACHR clears it on 36
    silent = sorted((tk, v[1]) for tk, v in dollar.items()
                    if v[1] >= NEVER_DOLLAR_MIN and v[0] == 0)
    if silent:
        print(f"\nnever written with a dollar sign ({NEVER_DOLLAR_MIN}+ matches):")
        for tk, n in sorted(silent, key=lambda x: -x[1]):
            tag = {"confirmed": "artefact", "ours": "our error",
                   "cleared": "checked, real"}.get(known.get(tk), "<- unexplained")
            print(f"    {tk:<7}{n:>6} matches, 0 with $   {tag}")

    fresh = [tk for tk, r, n in rows if tk not in known and abs(r - common) > 0.5]
    print(f"\n{len(fresh)} ticker(s) diverge without an entry in artifacts.json"
          + (f": {', '.join(fresh)}" if fresh else ""))

    # The page reads this to mark rows. It carries the ratio and the day count and nothing
    # else — a verdict field here would be a verdict computed by arithmetic, and the whole
    # point of artifacts.json is that verdicts come from reading the text.
    out = os.path.join(ROOT, "data", "divergence.json")
    with open(out, "w") as f:
        json.dump({
            "_note": ("Ratio of this project's count to ApeWisdom's, median across the days both "
                      "exist. Near `common` means the two rules agree — which is NOT the same as "
                      "either being right: EU sits at 1.20 and is the European Union. See "
                      "artifacts.json for what agreement cannot catch."),
            "days": seen_days, "depth": depth, "min_days": min_days,
            "min_mentions": min_mentions, "common": round(common, 3),
            # Every ticker counted, not only those clearing min_days. min_days governs
            # what the *ranking* trusts; a row seen twice still deserves to say so rather
            # than read as unchecked, and "checked on 2 days" is a different statement
            # from "never checked". A Deep riser is by definition new to the board, so
            # the filter that protects the ranking was excluding exactly the rows whose
            # provenance a reader is most likely to question.
            "tickers": {tk: {"days": seen_all.get(tk, 0),
                             **({"ratio": round(st.median(ratios[tk]), 3),
                                 "rated_days": len(ratios[tk])} if ratios.get(tk) else {}),
                             **({"dollar": dollar[tk][0], "matches": dollar[tk][1]}
                                if dollar[tk][1] else {})}
                        for tk in sorted(seen_all)},
            "min_days_for_ranking": min_days,
            "never_dollar": [tk for tk, n in silent],
            "never_dollar_min_matches": NEVER_DOLLAR_MIN,
        }, f, indent=1)
    print(f"{len(rows)} tickers -> data/divergence.json")


if __name__ == "__main__":
    main()
