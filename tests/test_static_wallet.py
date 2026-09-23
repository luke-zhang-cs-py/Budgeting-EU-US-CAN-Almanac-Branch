"""The second place the rate rules live, and the fixture that holds both.

`fxrates.py`, `money.py` and `fxcost.py` convert a purchase on the server.
`docs/app/fx.js` does the same in the browser, because the published static
wallet has no server to ask.

That is the second deliberate duplication in this project, after the
screenshot parser, and it is allowed on the same terms: it is *checked*.
`docs/app/cases.json` holds the cases and the answers, and there are two
tests here — the Python side still produces them, so the fixture cannot rot
while the reference implementation moves, and the JavaScript side produces
the same ones, run in a real browser against the same shipped rate file.

The cases are chosen to be the ways of being wrong rather than a sample:

  * a weekend, where the ECB publishes nothing;
  * Easter 2026, the longest gap in the series at five days;
  * a date past the newest rate on a weekday, which is refused, and on a
    weekend, which is not — because a Saturday's own rate is never coming;
  * a refund, where rounding half away from zero differs from rounding half
    up towards positive infinity by a cent;
  * both decimal conventions, and the three-digit tail that is grouping.

If either side changes a rule and not the other, one of these fails rather
than the two quietly disagreeing about what a purchase cost.
"""
import datetime as dt
import html
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import money   # noqa: E402
from fx import fxcost    # noqa: E402
from fx import fxrates   # noqa: E402

APP = os.path.join(ROOT, "docs", "app")
FIXTURE = os.path.join(APP, "cases.json")
FX_JS = os.path.join(APP, "fx.js")
RATES = os.path.join(APP, "fx_rates.csv")

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
def cases():
    with io.open(FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def rates_csv():
    with io.open(RATES, encoding="utf-8") as handle:
        return handle.read()


def in_browser(script, marker, rates_csv):
    """Run `script` after fx.js, with the shipped rates loaded, and return
    what it printed.

    --dump-dom rather than a JS test runner: there is no node on this machine,
    and the browser is the environment fx.js actually runs in, which makes it
    the right place to check it.
    """
    with io.open(FX_JS, encoding="utf-8") as handle:
        source = handle.read()

    page = ('<!doctype html><meta charset="utf-8"><body><pre id="out"></pre>\n'
            '<script>%s</script>\n'
            '<script>var RATES_CSV = %s; FX.loadRates(RATES_CSV);</script>\n'
            '<script>%s</script></body>'
            % (source, json.dumps(rates_csv), script))

    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, "check.html")
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(page)
        done = subprocess.run(
            [browser(), "--headless", "--disable-gpu", "--no-sandbox",
             "--no-first-run", "--no-default-browser-check",
             "--user-data-dir=" + os.path.join(folder, "profile"),
             "--virtual-time-budget=20000", "--dump-dom",
             "file:///" + path.replace("\\", "/")],
            capture_output=True, timeout=300)
        dom = done.stdout.decode("utf-8", "replace")
        noise = done.stderr.decode("utf-8", "replace")[-300:]
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    assert marker in dom, (
        "the page did not run -- a syntax error in fx.js would do this.\n"
        f"  exit {done.returncode}, {len(dom)} bytes of DOM\n  {noise}")
    return html.unescape(dom.split(marker, 1)[1].split("</pre>")[0]).strip()


# ----------------------------------------------------------- the fixture


def test_the_fixture_is_not_empty(cases):
    """A guard on the guard. Every test below loops over this file, and a loop
    over nothing passes -- which is how a structural test comes to cover
    nothing at all."""
    assert len(cases["lookups"]) >= 10
    assert len(cases["conversions"]) >= 5
    assert cases["estimates"] and cases["parses"]


def test_the_shipped_rates_are_the_ones_the_app_uses(rates_csv):
    """The page ships its own copy of the rate history. If that stops being
    the file the server reads, the two would answer differently for reasons
    no test here would otherwise catch."""
    header = rates_csv.splitlines()[0]
    assert header.startswith("date,"), "the shipped rates lost their header"
    for currency in money.TARGETS:
        assert currency in header, f"the shipped rates have no {currency}"
    assert len(rates_csv.splitlines()) > 7000, (
        "the shipped rate history is far shorter than the ECB series")


# ------------------------------------------------------- the Python side


def test_the_python_side_still_matches_the_fixture(cases):
    """So the fixture cannot rot while fxrates.py moves on.

    Read against the *shipped* rate file rather than the working cache in
    data/. Two reasons, and the second one is why CI went red: both sides
    then answer from the same bytes, so a stale snapshot cannot pass here
    and fail in the browser -- and data/ is gitignored, so on a fresh clone
    it does not exist at all and every lookup raised RateError.
    """
    fxrates.reset()
    data = APP

    for case in cases["lookups"]:
        on = dt.date.fromisoformat(case["on"])
        for currency, want in case["rates"].items():
            try:
                value, used = fxrates.rate(on, currency, directory=data)
                got = {"rate": str(value), "from": used.isoformat()}
            except fxrates.RateError:
                got = {"error": "RateError"}
            assert got == want, f"{case['on']} {currency}: {case['why']}"

    for case in cases["conversions"]:
        on = dt.date.fromisoformat(case["on"])
        for currency, want in case["out"].items():
            minor, value, used = fxrates.convert(case["cents"], on, currency,
                                                 directory=data)
            assert {"minor": minor, "rate": str(value),
                    "rateDate": used.isoformat()} == want, case["why"]

    for case in cases["estimates"]:
        got = fxcost.estimate(case["base"], case["rate"], case["feeBp"])
        assert got["converted_minor"] == case["converted"], case["why"]
        assert got["fee_minor"] == case["fee"], case["why"]
        assert got["total_minor"] == case["total"], case["why"]
        assert got["effective_rate"] == case["effective"], case["why"]

    for case in cases["parses"]:
        try:
            minor = money.parse(case["text"])
        except money.MoneyError:
            minor = None
        assert minor == case["minor"], case["why"]


# --------------------------------------------------- the JavaScript side


@pytest.mark.skipif(not browser(), reason="no browser to run the JS in")
def test_the_javascript_side_matches_the_fixture(cases, rates_csv):
    """The port, run in a real browser against the same cases and the same
    shipped rate file."""
    script = """
var CASES = %s;
var bad = [];

function same(a, b){ return JSON.stringify(a) === JSON.stringify(b); }
function check(why, mine, want){
  if (!same(mine, want)){
    bad.push(why + ' | js ' + JSON.stringify(mine) +
             ' | py ' + JSON.stringify(want));
  }
}

CASES.lookups.forEach(function (c) {
  Object.keys(c.rates).forEach(function (cur) {
    var mine;
    try {
      var got = FX.rate(c.on, cur);
      mine = { rate: got.rate, from: got.from };
    } catch (e) { mine = { error: 'RateError' }; }
    check(c.on + ' ' + cur + ': ' + c.why, mine, c.rates[cur]);
  });
});

CASES.conversions.forEach(function (c) {
  Object.keys(c.out).forEach(function (cur) {
    var got = FX.convertOn(c.cents, c.on, cur);
    check(c.cents + ' on ' + c.on + ' ' + cur + ': ' + c.why,
          { minor: got.minor, rate: got.rate, rateDate: got.rateDate },
          c.out[cur]);
  });
});

CASES.estimates.forEach(function (c) {
  var got = FX.estimate(c.base, c.rate, c.feeBp);
  check('estimate ' + c.why,
        { converted: got.convertedMinor, fee: got.feeMinor,
          total: got.totalMinor, effective: got.effectiveRate },
        { converted: c.converted, fee: c.fee, total: c.total,
          effective: c.effective });
});

CASES.parses.forEach(function (c) {
  check('parse ' + JSON.stringify(c.text) + ': ' + c.why,
        FX.parseMoney(c.text), c.minor);
});

document.getElementById('out').textContent =
  'RESULT ' + (bad.length ? bad.join(' || ') : 'ALL MATCH');
""" % json.dumps(cases)

    verdict = in_browser(script, "RESULT ", rates_csv)
    assert verdict == "ALL MATCH", (
        "the browser and the server disagree:\n  " +
        verdict.replace(" || ", "\n  "))


@pytest.mark.skipif(not browser(), reason="no browser to run the JS in")
def test_the_file_this_page_writes_imports_into_the_app(cases, rates_csv,
                                                        tmp_path):
    """The hand-off, in both real implementations.

    The static page cannot talk to the app — it has no server — so the CSV is
    the whole of the connection between them, and the amount column is where
    it goes wrong. The export wrote a grouped "1,234.56" into a
    comma-separated file until a purchase over a thousand went through it,
    which the far end would have read as two columns. This writes the file
    with the page's own code in a browser and reads it back with the app's
    own importer.
    """
    rows = [
        {"date": "2026-09-08", "description": "REWE SAGT DANKE",
         "category": "Groceries", "minor": 5230},
        {"date": "2026-08-14", "description": 'Cafe "Nord", Berlin',
         "category": "", "minor": 123456},        # over a thousand, and quoted
    ]
    script = ("var ROWS = %s;\n"
              "var COLUMNS = ['Date', 'Description', 'Amount', 'Currency'];\n"
              "var lines = [COLUMNS.join(',')];\n"
              "ROWS.forEach(function (r) {\n"
              "  var what = r.description + (r.category ? ' - ' + r.category : '');\n"
              "  var safe = /[\",\\n]/.test(what)\n"
              "    ? '\"' + what.replace(/\"/g, '\"\"') + '\"' : what;\n"
              "  lines.push([r.date, safe, '-' + FX.plain(r.minor), 'EUR'].join(','));\n"
              "});\n"
              "document.getElementById('out').textContent =\n"
              "  'CSV ' + JSON.stringify(lines.join('\\n') + '\\n');"
              % json.dumps(rows))

    text = json.loads(in_browser(script, "CSV ", rates_csv))
    assert text.startswith("Date,Description,Amount,Currency\n")
    assert "1,234.56" not in text, (
        "the export grouped an amount inside a comma-separated file")

    from domain import db          # noqa: E402
    from ingest import importers   # noqa: E402
    from domain import ledger      # noqa: E402

    shape = importers.preview(importers.sniff(text))
    assert shape["problems"] == []
    assert shape["unreadable"] == 0, (
        "the app could not read a row this page wrote")
    assert shape["mapping"]["expenses_positive"] is False

    connection = db.connect(str(tmp_path / "wallet.db"))
    added = importers.load(connection, shape, source="static-wallet.csv")
    assert added["added"] == len(rows)
    assert added["failed"] == []

    stored = {row["description"]: row["amount_eur"]
              for row in ledger.transactions(connection, limit=10)}
    assert stored == {
        "REWE SAGT DANKE - Groceries": -5230,
        'Cafe "Nord", Berlin': -123456,
    }, "money out, at the amount written, with the quoting survived"


@pytest.mark.skipif(not browser(), reason="no browser to run the JS in")
def test_that_check_would_notice_a_disagreement(cases, rates_csv):
    """The negative control. A cross-implementation check is worth nothing
    until it has been seen to fail, so this hands the browser a fixture with
    one wrong answer in it and asserts the comparison rejects it."""
    broken = json.loads(json.dumps(cases))
    for case in broken["conversions"]:
        for currency in case["out"]:
            case["out"][currency]["minor"] += 1
            break
        break

    script = """
var CASES = %s;
var bad = 0;
CASES.conversions.forEach(function (c) {
  Object.keys(c.out).forEach(function (cur) {
    if (FX.convertOn(c.cents, c.on, cur).minor !== c.out[cur].minor) bad += 1;
  });
});
document.getElementById('out').textContent = 'MISMATCHES ' + bad;
""" % json.dumps(broken)

    count = in_browser(script, "MISMATCHES ", rates_csv)
    assert count == "1", (
        f"a planted wrong answer produced {count} mismatches, not one -- "
        f"the comparison is not doing what it claims")
