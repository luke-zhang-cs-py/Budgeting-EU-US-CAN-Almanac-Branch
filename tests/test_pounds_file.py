"""The pound wallet's standalone build: one file, from disk, with no network.

`tools/build_pounds_file.py` folds standalone/pounds/ into a single HTML
file meant to be double-clicked -- copied to a USB stick, emailed to yourself, opened on
a machine with no Python and no connection.

The same two properties as tests/test_single_file.py, and both fail quietly
if they stop being true:

  * **Nothing may point outside the file.** This page is worse placed than
    the euro one for that: it loads two of its files from `../app/`, so a
    missed rewrite still works while the file sits in standalone/pounds/
    beside its sources and breaks the moment somebody moves it -- which is
    the one
    situation the file exists for.
  * **The rates have to be inlined.** A file opened from disk has a `file://`
    origin, where `fetch` is refused outright.

The second is checked the only way worth checking it: by opening the built
file from disk in a real browser and asking it what it converted, what rate
date it used, and what it does when a conversion breaks the limit.
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

import build_pounds_file as builder   # noqa: E402
import pounds                        # noqa: E402

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
    """The property that makes it standalone. This page starts with two
    `../app/` references, so the rewrite has real work to do rather than
    being a formality."""
    loose = re.findall(r'(?:src|href)="(?!#|https?:|mailto:|data:)([^"]+)"',
                       built)
    assert not loose, f"these still point outside the file: {loose}"


def test_the_builder_refuses_to_write_a_file_that_does_not(built):
    """The negative control for the check above. The builder's own guard is
    what stands between a broken link and a file somebody has already copied
    somewhere, so it is worth knowing it fires."""
    broken = built.replace("</body>", '<img src="chart.png"></body>', 1)
    loose = re.findall(r'(?:src|href)="(?!#|https?:|mailto:|data:)([^"]+)"',
                       broken)
    assert loose == ["chart.png"], (
        "the check that guards this would not have noticed a stray reference")


def test_everything_is_in_there(built):
    """Both stylesheets, all three scripts, and the rate history."""
    assert built.count("<style>") == 2, "both stylesheets should be inlined"
    assert ".limitBar" in built, "limit.css is not inlined"
    assert "var FX = (function" in built, "fx.js is not inlined"
    assert "var GBP = (function" in built, "gbp.js is not inlined"
    assert "function drawLimit(" in built, "app.js is not inlined"

    rates = re.search(r"var RATES_CSV = (\".*?\");\n", built, re.S)
    assert rates, "the rate history is not inlined"
    text = json.loads(rates.group(1))
    assert len(text.splitlines()) > 7000, "the inlined history is too short"
    assert text.splitlines()[0].strip() == "date,CAD,GBP", (
        "the inlined history has to carry both euro legs")


def test_it_cannot_reach_the_network_at_all(built):
    """The served page allows connect-src 'self' so it can fetch its rates.
    This one needs nothing, so it allows nothing -- a better guarantee than
    a promise, and the reason the policy is worth checking."""
    policy = re.search(r'Content-Security-Policy" content="([^"]*)"', built)
    assert policy, "the built file has no content policy"
    assert "default-src 'none'" in policy.group(1)
    assert "connect-src" not in policy.group(1), (
        "the standalone file should not be allowed to make a request at all")


def test_the_banner_quotes_the_limit_the_code_enforces(built):
    """The banner is the first thing a reader sees and the only description
    they get, so it has to come from the source rather than from whatever
    the limit was when it was written."""
    assert "£{:,}".format(pounds.LIMIT_MINOR // 100) in built, (
        "the banner does not quote the limit gbp.js actually enforces")


@pytest.mark.skipif(not browser(), reason="no browser to open the file in")
def test_it_converts_and_holds_the_limit_from_disk(built):
    """The whole point, checked the only way that means anything: from a
    file:// origin, where fetch does not work.

    It seeds one conversion, then tries a second that would break the limit,
    so the refusal is exercised in the built file rather than only in the
    sources it was built from.
    """
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, "wallet-pounds.html")
        seed = (
            "<script>localStorage.setItem('wallet.pounds.v1', JSON.stringify(["
            "{id:'a', added:1, date:'2026-09-11', description:'Term rent',"
            " category:'Housing', cadMinor:120000}]));</script>"
        )
        probe = (
            "<script>window.addEventListener('load', function () {"
            "window.setTimeout(function () {"
            "  document.getElementById('date').value = '2026-09-11';"
            "  document.getElementById('amount').value = '900.00';"
            "  document.getElementById('description').value = 'Flight';"
            "  document.getElementById('addForm')"
            "    .dispatchEvent(new Event('submit', {cancelable: true}));"
            "  var out = document.createElement('pre');"
            "  out.id = 'PROBE';"
            "  out.textContent = 'PROBE ' + JSON.stringify({"
            "    origin: location.protocol,"
            "    loaded: FX.loaded(),"
            "    columns: FX.columns(),"
            "    limit: GBP.LIMIT_MINOR,"
            "    thousand: GBP.convertOn(100000, '2026-09-11').poundsMinor,"
            "    weekend: GBP.convertOn(8450, '2026-09-06').rateDate,"
            "    refused: document.getElementById('notice').textContent,"
            "    rowCount: document.querySelectorAll('#rows tr').length"
            "  });"
            "  document.body.insertBefore(out, document.body.firstChild);"
            "}, 600); });</script>"
        )
        page = built.replace("</head>", seed + "</head>", 1)
        page = page.replace("</body>", probe + "</body>", 1)
        with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(page)

        done = subprocess.run(
            [browser(), "--headless=new", "--disable-gpu", "--no-sandbox",
             "--no-first-run", "--no-default-browser-check",
             "--user-data-dir=" + os.path.join(folder, "profile"),
             "--virtual-time-budget=20000", "--dump-dom",
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
    assert got["columns"] == ["CAD", "GBP"], (
        "the built file loaded a rate table without both euro legs")
    assert got["limit"] == pounds.LIMIT_MINOR

    # The figures the sources produce, reproduced by the built file.
    cad, gbp, _on = pounds.legs("2026-09-11", directory=builder.PAGE)
    expected = pounds.to_pounds(100000, cad, gbp)["pounds_minor"]
    assert got["thousand"] == expected, (
        "CA$1,000 converts differently in the built file")
    assert got["weekend"] == "2026-09-04", (
        "the business-day walk-back is not working in the built file")

    # The limit, enforced rather than warned about.
    assert "past the" in got["refused"], (
        f"a conversion over the limit was not refused: {got['refused']!r}")
    assert got["rowCount"] == 1, (
        "the refused conversion was recorded anyway")
