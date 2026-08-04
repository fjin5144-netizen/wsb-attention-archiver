"""End-to-end: does the rendered page agree with the archive?

The earlier version of this test loaded the page with data/events.json in place and
compared the DOM count against that same file. The browser reads the file, so it was
comparing an artefact with itself — it agreed perfectly while both were missing 69 of
239 events.

It now checks both paths separately against a number computed from the snapshots:

  * with events.json present   — the precomputed path
  * with events.json removed   — the client-side scan that exists to catch a bad
                                 precompute, and which is only useful while it stays
                                 an independent implementation

Skips rather than fails when headless Chrome is absent.
"""
import contextlib, json, os, re, shutil, socket, subprocess, sys, threading, time
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_validation import archive_events  # noqa: E402

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
]


def find_chrome():
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    return shutil.which("google-chrome") or shutil.which("chromium")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def serve():
    from http.server import HTTPServer, SimpleHTTPRequestHandler

    class Quiet(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=ROOT, **kw)

        def log_message(self, *a):
            pass

    port = free_port()
    httpd = HTTPServer(("127.0.0.1", port), Quiet)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield port
    finally:
        httpd.shutdown()


def dom_event_count(chrome, port):
    """Render the Aftermath tab and read the count the page itself reports."""
    out = subprocess.run(
        [chrome, "--headless", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=180000", "--dump-dom",
         f"http://127.0.0.1:{port}/index.html#aftermath"],
        capture_output=True, text=True, timeout=420).stdout
    m = re.search(r'id="labStat".*?<b>(\d+)</b>', out, re.S)
    assert m, "could not find the event count in the rendered page"
    return int(m.group(1))


@pytest.mark.slow
def test_both_paths_agree_with_the_archive():
    chrome = find_chrome()
    if not chrome:
        pytest.skip("headless Chrome not available")

    expected = len(archive_events())
    events = os.path.join(ROOT, "data", "events.json")
    stashed = events + ".test-stash"

    with serve() as port:
        assert os.path.exists(events), "run scripts/precompute_events.py first"
        assert dom_event_count(chrome, port) == expected, \
            "precomputed path disagrees with the archive"

        os.rename(events, stashed)
        try:
            assert dom_event_count(chrome, port) == expected, \
                "client-side fallback disagrees with the archive"
        finally:
            os.rename(stashed, events)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-s"]))
