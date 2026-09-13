"""The standalone build: one file, opened from disk, with no network.

`tools/build_single_file.py` folds docs/app/ into a single HTML file meant to
be double-clicked — copied to a USB stick, emailed to yourself, opened on a
machine with no Python and no connection.

Two things make that work, and both fail quietly if they stop being true:

  * **Nothing may point outside the file.** A stray `src="fx.js"` still loads
    while the file sits in docs/app/ beside its sources, and only breaks once
    somebody moves it — which is the one situation the file exists for. The
    builder refuses to write in that case; this checks it would.
  * **The rates have to be inlined.** A file opened from disk has a `file://`
    origin, where `fetch` is refused outright, so a page still reaching for
    its rate file comes up with no conversions at all.

The second is checked the only way worth checking it: by opening the built
file from disk in a real browser and asking it whether it has rates.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_single_file as builder   # noqa: E402

BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)


def browser():
    for path in BROWSERS:
        if os.path.isfile(path):
            return path
    return shutil.which("google-chrome") or shutil.which("chromium")


@pytest.fixture(scope="module")
def built():
    page, _span = builder.build()
    return page


def test_nothing_points_outside_the_file(built):
    """The property that makes it standalone. A relative src or href is only
    broken once the file is moved, which is exactly when it is being used."""
    loose = re.findall(r'(?:src|href)="(?!#|https?:|mailto:|data:)([^"]+)"',
                       built)
    assert not loose, f"these still point outside the file: {loose}"


def test_the_builder_refuses_to_write_a_file_that_does_not(tmp_path):
    """The negative control for the check above.

    The builder's own guard is what stands between a broken link and a file
    somebody has already copied somewhere, so it is worth knowing it fires.
    """
    page, _ = builder.build()
    broken = page.replace("</body>", '<img src="chart.png"></body>', 1)
    loose = re.findall(r'(?:src|href)="(?!#|https?:|mailto:|data:)([^"]+)"',
                       broken)
    assert loose == ["chart.png"], (
        "the check that guards this would not have noticed a stray reference")


def test_everything_is_in_there(built):
    """The stylesheet, both scripts and the rate history."""
    assert "<style>" in built and "</style>" in built
    assert "var FX = (function" in built, "fx.js is not inlined"
    assert "function drawRows()" in built, "app.js is not inlined"

    rates = re.search(r"var RATES_CSV = (\".*?\");\n", built, re.S)
    assert rates, "the rate history is not inlined"
    text = json.loads(rates.group(1))
    assert len(text.splitlines()) > 7000, "the inlined history is too short"
    assert text.splitlines()[0].startswith("date,"), "it lost its header"


def test_it_cannot_reach_the_network_at_all(built):
    """The served page allows connect-src 'self' so it can fetch its rates.
    This one needs nothing, so it allows nothing -- which is a better
    guarantee than a promise, and the reason the policy is worth checking."""
    policy = re.search(r'Content-Security-Policy" content="([^"]*)"', built)
    assert policy, "the built file has no content policy"
    assert "default-src 'none'" in policy.group(1)
    assert "connect-src" not in policy.group(1), (
        "the standalone file should not be allowed to make a request at all")


@pytest.mark.skipif(not browser(), reason="no browser to open the file in")
def test_it_converts_when_opened_from_disk(built):
    """The whole point, checked the only way that means anything: from a
    file:// origin, where fetch does not work."""
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, "wallet.html")
        probe = (
            "<script>window.addEventListener('load', function () {"
            "window.setTimeout(function () {"
            "  var out = document.createElement('pre');"
            "  out.id = 'PROBE';"
            "  out.textContent = 'PROBE ' + JSON.stringify({"
            "    origin: location.protocol,"
            "    loaded: FX.loaded(),"
            "    cad: FX.convertOn(5230, '2026-09-08', 'CAD').minor,"
            "    from: FX.convertOn(5230, '2026-09-06', 'CAD').rateDate"
            "  });"
            "  document.body.insertBefore(out, document.body.firstChild);"
            "}, 400); });</script>"
        )
        with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(built.replace("</body>", probe + "</body>", 1))

        done = subprocess.run(
            [browser(), "--headless=new", "--disable-gpu", "--no-sandbox",
             "--no-first-run", "--no-default-browser-check",
             "--user-data-dir=" + os.path.join(folder, "profile"),
             "--virtual-time-budget=15000", "--dump-dom",
             "file:///" + path.replace("\\", "/")],
            capture_output=True, timeout=300)
        dom = done.stdout.decode("utf-8", "replace")
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    found = re.search(r"PROBE (\{.*?\})</pre>", dom, re.S)
    assert found, (
        f"the file did not run when opened from disk; {len(dom)} bytes of DOM")

    import html as htmllib
    got = json.loads(htmllib.unescape(found.group(1)))
    assert got["origin"] == "file:", "this did not test a file:// origin"
    assert got["loaded"] is True, (
        "opened from disk, the page has no rates -- the history is not "
        "inlined, or boot() is still fetching it")
    assert got["cad"] == 8385, (
        "the worked example converts differently in the built file")
    assert got["from"] == "2026-09-04", (
        "the business-day walk-back is not working in the built file")
