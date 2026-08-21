"""GitHub Pages must not be allowed to hide the price shards.

Pages builds with Jekyll unless told otherwise, and Jekyll silently drops every path
beginning with an underscore. data/px/_dates.json is the shared trading calendar that
every one of the 1,962 per-ticker shards indexes into — `{"i": 412, "c": [...]}` means
nothing without it — so its absence took the chart out of 370 of the 493 rows on the
board while the site looked perfectly healthy.

It survived because it cannot be reproduced locally: python -m http.server serves the
file, so every check during development passed. The only symptom was two 404s in the
browser console, and the only fix is a .nojekyll file at the repository root.

This asserts the guard exists rather than the symptom is absent, because the symptom is
invisible from here.
"""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_nojekyll_exists_so_underscore_paths_are_served():
    assert os.path.exists(os.path.join(ROOT, ".nojekyll")), (
        "No .nojekyll at the repository root. GitHub Pages will run Jekyll, and Jekyll "
        "drops every path starting with an underscore — including data/px/_dates.json, "
        "without which no price shard can be decoded.")


def test_nothing_but_the_calendar_relies_on_a_leading_underscore():
    """A second underscore-named file added later would break the same way, quietly.

    .nojekyll covers it, but the file is one `git rm` away from being gone, so this names
    what is at stake rather than leaving it to a comment.
    """
    px = os.path.join(ROOT, "data", "px")
    if not os.path.isdir(px):
        return
    under = sorted(n for n in os.listdir(px) if n.startswith("_"))
    assert under == ["_dates.json", "_ends.json", "_missing.json"], (
        f"data/px underscore files changed: {under}. Each is invisible to GitHub Pages "
        f"unless .nojekyll is present; if one is added, check it is actually served.")
    with open(os.path.join(px, "_dates.json")) as f:
        dates = json.load(f)
    assert len(dates) > 500 and dates == sorted(set(dates)), (
        "_dates.json must be a sorted, unique trading calendar — every shard indexes into it")
