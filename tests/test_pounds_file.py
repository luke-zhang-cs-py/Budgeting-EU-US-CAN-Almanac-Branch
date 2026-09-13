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


def drive(built, script, seed=None):
    """Run `script` inside the built file, opened from disk, and return what
    it reported.

    The page's own globals are in scope: every script in the standalone
    build is inline, so they all share one scope. That is what lets these
    call csv() and importCsv() directly rather than clicking a file picker,
    which headless Chrome will not do.
    """
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, "wallet-pounds.html")
        probe = (
            "<script>window.addEventListener('load', function () {"
            "window.setTimeout(function () {"
            "  var report;"
            "  try { report = (function () {" + script + "}()); }"
            "  catch (bad) { report = { error: bad.message }; }"
            "  var out = document.createElement('pre');"
            "  out.id = 'PROBE';"
            "  out.textContent = 'PROBE ' + JSON.stringify(report);"
            "  document.body.insertBefore(out, document.body.firstChild);"
            "}, 600); });</script>"
        )
        page = built
        if seed:
            page = page.replace(
                "</head>",
                "<script>localStorage.setItem('wallet.pounds.v1', %s);"
                "</script></head>" % json.dumps(json.dumps(seed)), 1)
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
    assert found, f"the page did not report; {len(dom)} bytes of DOM"
    import html as htmllib
    got = json.loads(htmllib.unescape(found.group(1)))
    assert "error" not in got, got.get("error")
    return got


# A description with a comma and a quote in it, because that is what the
# quoting rules on both sides exist for, and what a merchant name on a
# Belfast statement actually looks like.
AWKWARD = 'Tesco Metro, Botanic Ave "the wee one"'

ROUND_TRIP_SEED = [
    {"id": "r1", "added": 1, "date": "2026-09-11",
     "description": "Elms BT9 rent", "category": "Housing", "cadMinor": 62000},
    {"id": "r2", "added": 2, "date": "2026-09-06", "description": AWKWARD,
     "category": "Groceries", "cadMinor": 8450},
    {"id": "r3", "added": 3, "date": "2026-09-04", "description": "SIM + data",
     "category": "", "cadMinor": 3200},
]


@pytest.mark.skipif(not browser(), reason="no browser to open the file in")
def test_an_export_imports_back_into_the_same_rows(built):
    """The property that makes the pair worth having. A CSV you can write but
    not read back is a dead end, and quoting is where that usually breaks --
    hence a description carrying both a comma and a quote."""
    got = drive(built, """
        var text = csv();
        var shape = function () {
          return state.rows.slice().sort(function (a, b) {
            return a.date < b.date ? -1 : a.date > b.date ? 1 : 0;
          }).map(function (r) {
            return [r.date, r.description, r.category, r.cadMinor];
          });
        };
        var before = shape();
        var beforeSpent = spent();
        state.rows = [];
        save();
        importCsv(text);
        return {
          csv: text,
          before: before,
          after: shape(),
          beforeSpent: beforeSpent,
          afterSpent: spent(),
          notice: document.getElementById('notice').textContent
        };
    """, seed=ROUND_TRIP_SEED)

    assert got["after"] == got["before"], (
        "the rows did not survive a round trip through the CSV")
    assert got["afterSpent"] == got["beforeSpent"], (
        "the pounds changed across a round trip")
    assert AWKWARD in [row[1] for row in got["after"]], (
        "the quoted description did not come back intact")
    assert '"' in got["csv"], "the export did not quote the awkward field"
    assert "Imported 3 row(s)" in got["notice"], got["notice"]


@pytest.mark.skipif(not browser(), reason="no browser to open the file in")
def test_import_reads_the_dollars_and_recomputes_the_pounds(built):
    """The GBP column in an export is derived. A file carrying a wrong one
    must not be believed -- the dollars are the record, the pounds are a
    conclusion drawn from them and the rate history this copy has."""
    got = drive(built, """
        importCsv('Date,Description,Category,CAD,GBP,Markup,Rate from\\n' +
                  '2026-09-11,Rent,Housing,1000.00,999.99,0.00,1999-01-04\\n');
        var r = state.rows[0];
        return {
          rows: state.rows.length,
          cadMinor: r.cadMinor,
          pounds: converted(r).gbp,
          expected: GBP.convertOn(100000, '2026-09-11').poundsMinor,
          rateDate: converted(r).rateDate
        };
    """)
    assert got["rows"] == 1
    assert got["cadMinor"] == 100000
    assert got["pounds"] == got["expected"], (
        "the imported row kept the CSV's GBP figure instead of recomputing")
    assert got["pounds"] != 99999, "it believed the file's derived column"
    assert got["rateDate"] == "2026-09-11", (
        "it kept the file's 'Rate from' instead of looking the rate up")


@pytest.mark.skipif(not browser(), reason="no browser to open the file in")
def test_an_import_over_the_limit_is_kept_rather_than_trimmed(built):
    """record() refuses a conversion past the limit; an import does not.

    The two are different questions. Typing one in is a decision not yet
    made. Importing is a record of decisions already taken, and dropping the
    rows that did not fit would make the page understate what was spent --
    the failure a budget tool can least afford.
    """
    got = drive(built, """
        importCsv('Date,Description,CAD\\n' +
                  '2026-09-11,Rent,1200.00\\n' +
                  '2026-09-11,Flights,900.00\\n' +
                  '2026-09-11,Fees,400.00\\n');
        return {
          rows: state.rows.length,
          spent: spent(),
          remaining: GBP.remaining(spent()),
          barClass: document.getElementById('limitBar').className,
          state: document.getElementById('limitState').textContent,
          notice: document.getElementById('notice').textContent
        };
    """)
    assert got["rows"] == 3, "rows were dropped to fit the limit"
    assert got["remaining"] < 0, "this case was meant to land over the limit"
    assert "over" in got["barClass"], "the bar does not show the overspend"
    assert "over the limit" in got["state"]
    assert "over the" in got["notice"] and "imported anyway" in got["notice"], (
        f"the notice does not say it went over: {got['notice']!r}")


@pytest.mark.skipif(not browser(), reason="no browser to open the file in")
def test_a_file_without_the_columns_is_refused_and_changes_nothing(built):
    """A wrong file should leave the ledger alone and say why, rather than
    importing nothing and reporting success."""
    got = drive(built, """
        var before = state.rows.length;
        importCsv('Foo,Bar\\n1,2\\n');
        return {
          before: before,
          after: state.rows.length,
          notice: document.getElementById('notice').textContent,
          kind: document.getElementById('notice').className
        };
    """, seed=ROUND_TRIP_SEED)
    assert got["after"] == got["before"] == 3, "a bad file changed the rows"
    assert "no Date and CAD columns" in got["notice"], got["notice"]
    assert "bad" in got["kind"], "a refusal should not look like success"


@pytest.mark.skipif(not browser(), reason="no browser to open the file in")
def test_unreadable_rows_are_counted_rather_than_dropped_silently(built):
    """Skipping is fine; skipping quietly is not. A file half of which did
    not load should say so, or the total looks like the whole story."""
    got = drive(built, """
        importCsv('Date,Description,CAD\\n' +
                  '2026-09-11,Good,100.00\\n' +
                  'not-a-date,Bad,50.00\\n' +
                  '2026-09-11,No amount,\\n');
        return {
          rows: state.rows.length,
          notice: document.getElementById('notice').textContent
        };
    """)
    assert got["rows"] == 1
    assert "skipped 2" in got["notice"], got["notice"]


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
