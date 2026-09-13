"""Canadian dollars into pounds, in both languages, against one fixture.

`pounds.py` converts CAD into GBP through the euro and takes CIBC's markup
off the Canadian side. `standalone/pounds/gbp.js` does the same in the browser,
because the standalone file has no server to ask.

That is the third deliberate duplication in this project, after the
screenshot parser and `docs/app/fx.js`, and it is allowed on the same terms:
it is *checked*. `standalone/pounds/cases.json` holds the cases and the
answers,
and both sides are held to them -- Python so the fixture cannot rot while
the reference implementation moves, and JavaScript in a real browser against
the same shipped rate file.

Two things here are not just "the same answer twice", and they are the
reason the file is worth reading:

  * **The fee divides.** CIBC converts and then adds 2.5% to the Canadian
    figure, so buying pounds *with* dollars inverts that. The tempting
    shortcut -- take 2.5% off the pounds -- is a different number, and the
    round-trip through `fxcost.estimate` is what tells the two apart: if
    converting C dollars gives P pounds, then estimating P pounds on a 2.5%
    card has to bill C dollars back. `test_the_shortcut_would_fail_this` is
    the negative control that proves the round-trip can fail.

  * **The limit is a limit.** 1,000 exactly fits; a penny more does not.

The cases are chosen for the rules they exercise: a Sunday and a Saturday
for the business-day walk-back, a bank-holiday Monday, the first day of the
series in 1999, and amounts down to a single cent, where rounding half away
from zero is the whole of the answer.
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
from decimal import Decimal, ROUND_HALF_UP

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fxcost    # noqa: E402
import fxrates   # noqa: E402
import money     # noqa: E402
import pounds    # noqa: E402

PAGE = os.path.join(ROOT, "standalone", "pounds")
FIXTURE = os.path.join(PAGE, "cases.json")
GBP_JS = os.path.join(PAGE, "gbp.js")
FX_JS = os.path.join(ROOT, "docs", "app", "fx.js")
RATES = os.path.join(PAGE, "fx_rates.csv")

# The round-trip closes to within a cent or two rather than exactly, and the
# slack is arithmetic rather than sloppiness: the pounds are rounded to a
# whole penny (worth up to ~0.9 cents at these rates) and the cross rate the
# estimate re-converts with is the displayed six-decimal one. Two cents is
# comfortably inside that and nowhere near the ~2.5% a wrong fee direction
# would move the answer -- test_the_shortcut_would_fail_this pins the gap.
ROUND_TRIP_SLACK = 2

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
    """Run `script` after fx.js and gbp.js, with the shipped rates loaded.

    --dump-dom rather than a JS test runner: there is no node on this
    machine, and the browser is the environment gbp.js actually runs in,
    which makes it the right place to check it.
    """
    with io.open(FX_JS, encoding="utf-8") as handle:
        fx_source = handle.read()
    with io.open(GBP_JS, encoding="utf-8") as handle:
        gbp_source = handle.read()

    page = ('<!doctype html><meta charset="utf-8"><body><pre id="out"></pre>\n'
            '<script>%s</script>\n'
            '<script>var RATES_CSV = %s; FX.loadRates(RATES_CSV);</script>\n'
            '<script>%s</script>\n'
            '<script>%s</script></body>'
            % (fx_source, json.dumps(rates_csv), gbp_source, script))

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
        "the page did not run -- a syntax error in gbp.js would do this.\n"
        f"  exit {done.returncode}, {len(dom)} bytes of DOM\n  {noise}")
    return html.unescape(dom.split(marker, 1)[1].split("</pre>")[0]).strip()


# ----------------------------------------------------------- the fixture


def test_the_fixture_is_not_empty(cases):
    """A guard on the guard. Every test below loops over this file, and a
    loop over nothing passes -- which is how a structural test comes to
    cover nothing at all."""
    assert len(cases["cases"]) >= 40
    assert cases["limitMinor"] == pounds.LIMIT_MINOR
    assert cases["feeBp"] == pounds.CIBC_FEE_BP
    assert not [case for case in cases["cases"] if "error" in case], (
        "every case date should resolve against the shipped rates")


def test_the_shipped_rates_carry_both_legs(rates_csv):
    """A cross rate needs EUR->CAD and EUR->GBP. The app's own rate file has
    no GBP column at all, so a page pointed at the wrong one would fail on
    every conversion rather than quietly."""
    header = rates_csv.splitlines()[0].strip()
    assert header == "date,CAD,GBP", (
        f"the pound wallet's rate file should carry both legs, got {header}")
    assert len(rates_csv.splitlines()) > 7000, "the history is too short"


# --------------------------------------------------- the two implementations


def test_python_still_produces_the_fixture(cases):
    """The fixture is generated from pounds.py, so this is what stops it
    rotting silently when the reference implementation moves."""
    for case in cases["cases"]:
        got = pounds.to_pounds(case["cadMinor"], case["cadRate"],
                               case["gbpRate"])
        where = f"{case['cadMinor']} cents on {case['on']}"
        assert got["reference_minor"] == case["referenceMinor"], where
        assert got["pounds_minor"] == case["poundsMinor"], where
        assert got["fee_minor"] == case["feeMinor"], where
        assert got["cross"] == case["cross"], where
        assert got["effective_rate"] == case["effectiveRate"], where


def test_python_looks_up_the_same_rate_dates(cases):
    """The walk-back, not the arithmetic: a weekend case has to land on the
    Friday, and both legs on the same day."""
    for case in cases["cases"]:
        _cad, _gbp, rate_date = pounds.legs(case["on"], directory=PAGE)
        assert rate_date.isoformat() == case["rateDate"], case["on"]


@pytest.mark.skipif(not browser(), reason="no browser to run gbp.js in")
def test_the_browser_produces_the_same_answers(cases, rates_csv):
    """The other half of the duplication, run where it actually runs."""
    script = """
    var CASES = %s;
    var out = [];
    CASES.forEach(function (c) {
      try {
        var got = GBP.toPounds(c.cadMinor, c.cadRate, c.gbpRate);
        out.push({
          on: c.on, cadMinor: c.cadMinor,
          referenceMinor: got.referenceMinor, poundsMinor: got.poundsMinor,
          feeMinor: got.feeMinor, cross: got.cross,
          effectiveRate: got.effectiveRate,
          rateDate: GBP.legs(c.on).from
        });
      } catch (bad) {
        out.push({ on: c.on, cadMinor: c.cadMinor, error: bad.message });
      }
    });
    document.getElementById('out').textContent = 'RESULT ' +
      JSON.stringify(out);
    """ % json.dumps(cases["cases"])

    got = json.loads(in_browser(script, "RESULT", rates_csv))
    assert len(got) == len(cases["cases"])

    for mine, theirs in zip(cases["cases"], got):
        where = f"{theirs['cadMinor']} cents on {theirs['on']}"
        assert "error" not in theirs, f"{where}: {theirs.get('error')}"
        assert theirs["referenceMinor"] == mine["referenceMinor"], where
        assert theirs["poundsMinor"] == mine["poundsMinor"], where
        assert theirs["feeMinor"] == mine["feeMinor"], where
        assert theirs["cross"] == mine["cross"], where
        assert theirs["effectiveRate"] == mine["effectiveRate"], where
        assert theirs["rateDate"] == mine["rateDate"], where


# ------------------------------------------------------- the fee direction


def test_the_fee_direction_round_trips_through_fxcost(cases):
    """The property that makes the direction checkable rather than a matter
    of opinion: if converting C dollars buys P pounds, then buying P pounds
    on a 2.5% card has to bill C dollars back."""
    for case in cases["cases"]:
        if case["cadMinor"] < 100:
            continue        # a penny of slack swamps a one-cent conversion
        back = fxcost.estimate(case["poundsMinor"], case["cross"],
                               pounds.CIBC_FEE_BP)
        drift = abs(back["total_minor"] - case["cadMinor"])
        assert drift <= ROUND_TRIP_SLACK, (
            f"{case['cadMinor']} cents on {case['on']} converted to "
            f"{case['poundsMinor']} pence, which bills back "
            f"{back['total_minor']} -- off by {drift}")


def test_the_shortcut_would_fail_this(cases):
    """The negative control.

    Taking 2.5% off the pounds instead of adding it on the Canadian side is
    the plausible wrong answer, and the whole value of the round-trip above
    is that it rejects it. If this ever stops failing, the round-trip has
    stopped testing anything.
    """
    worst = 0
    for case in cases["cases"]:
        if case["cadMinor"] < 100:
            continue
        shortcut = int((Decimal(case["referenceMinor"]) * Decimal("0.975"))
                       .quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        back = fxcost.estimate(shortcut, case["cross"], pounds.CIBC_FEE_BP)
        worst = max(worst, abs(back["total_minor"] - case["cadMinor"]))
    assert worst > ROUND_TRIP_SLACK, (
        "the shortcut round-trips just as well, so the round-trip test is "
        "not telling the two apart")


# ------------------------------------------------------------ the inverse


def test_from_pounds_inverts_to_pounds_to_within_a_penny(cases):
    """`from_pounds` answers "what would I have to convert to get this",
    which is what the refusal message offers when a conversion would break
    the limit. Rounding makes it a left inverse only to within a penny, so
    that is the property stated -- claiming an exact one would be a test
    that fails on an unlucky rate rather than on a real mistake."""
    for case in cases["cases"]:
        if case["poundsMinor"] < 100:
            continue
        needed = pounds.from_pounds(case["poundsMinor"], case["cadRate"],
                                    case["gbpRate"])
        again = pounds.to_pounds(needed, case["cadRate"], case["gbpRate"])
        drift = abs(again["pounds_minor"] - case["poundsMinor"])
        assert drift <= 1, (
            f"{case['poundsMinor']} pence on {case['on']} needs {needed} "
            f"cents, which buys {again['pounds_minor']} back")


def test_the_offer_in_a_refusal_is_not_over_the_limit(cases):
    """The figure the page offers when it refuses a conversion has to be one
    it would then accept. A penny over would be a worse failure than no
    figure at all -- you would type it in and be refused again."""
    for case in cases["cases"]:
        left = pounds.LIMIT_MINOR - 250        # a plausible remaining budget
        needed = pounds.from_pounds(left, case["cadRate"], case["gbpRate"])
        got = pounds.to_pounds(needed, case["cadRate"], case["gbpRate"])
        assert pounds.fits(pounds.LIMIT_MINOR - left, got["pounds_minor"]), (
            f"on {case['on']} the page would offer {needed} cents, which "
            f"buys {got['pounds_minor']} pence against {left} left")


@pytest.mark.skipif(not browser(), reason="no browser to run gbp.js in")
def test_the_browser_inverts_the_same_way(cases, rates_csv):
    script = """
    var CASES = %s;
    var out = CASES.map(function (c) {
      return GBP.fromPounds(c.poundsMinor, c.cadRate, c.gbpRate);
    });
    document.getElementById('out').textContent = 'RESULT ' +
      JSON.stringify(out);
    """ % json.dumps(cases["cases"])

    got = json.loads(in_browser(script, "RESULT", rates_csv))
    for case, theirs in zip(cases["cases"], got):
        mine = pounds.from_pounds(case["poundsMinor"], case["cadRate"],
                                  case["gbpRate"])
        assert theirs == mine, f"{case['poundsMinor']} pence on {case['on']}"


# -------------------------------------------------------------- the limit


def test_the_limit_is_a_thousand_pounds():
    assert pounds.LIMIT_MINOR == 100000


def test_exactly_on_the_limit_fits():
    """1,000.00 of a 1,000 budget is spent, not overspent."""
    assert pounds.fits(0, 100000)
    assert pounds.fits(99999, 1)
    assert not pounds.fits(99999, 2)
    assert not pounds.fits(0, 100001)


def test_remaining_reports_an_overspend_rather_than_clamping():
    """A total that silently stops at zero is a total that lies."""
    assert pounds.remaining(0) == 100000
    assert pounds.remaining(100000) == 0
    assert pounds.remaining(100500) == -500


@pytest.mark.skipif(not browser(), reason="no browser to run gbp.js in")
def test_the_browser_agrees_about_the_limit(rates_csv):
    """The limit is the point of the file, so it is checked on both sides
    rather than assumed to have been ported."""
    script = """
    document.getElementById('out').textContent = 'RESULT ' + JSON.stringify({
      limit: GBP.LIMIT_MINOR,
      feeBp: GBP.CIBC_FEE_BP,
      exactlyOn: GBP.fits(0, 100000),
      aPennyOver: GBP.fits(0, 100001),
      lastPenny: GBP.fits(99999, 1),
      remaining: GBP.remaining(0),
      spentOut: GBP.remaining(100000),
      overspent: GBP.remaining(100500)
    });
    """
    got = json.loads(in_browser(script, "RESULT", rates_csv))
    assert got["limit"] == pounds.LIMIT_MINOR
    assert got["feeBp"] == pounds.CIBC_FEE_BP
    assert got["exactlyOn"] is True
    assert got["aPennyOver"] is False
    assert got["lastPenny"] is True
    assert got["remaining"] == pounds.remaining(0)
    assert got["spentOut"] == pounds.remaining(100000)
    assert got["overspent"] == pounds.remaining(100500)


# ------------------------------------------------------------- the refusals


def test_convert_on_carries_the_rate_date_and_the_lag():
    """The one call the page makes. The lag is not decoration: it is what
    lets a row say a Sunday purchase was converted at Friday's rate."""
    fxrates.reset()
    try:
        got = pounds.convert_on(8450, "2026-09-06", directory=PAGE)
        assert got["on"] == "2026-09-06"
        assert got["rate_date"] == "2026-09-04", "a Sunday has no rate"
        assert got["lag_days"] == 2
        assert got["pounds_minor"] > 0
    finally:
        fxrates.reset()


@pytest.mark.parametrize("given, expect", [
    (dt.date(2026, 9, 11), dt.date(2026, 9, 11)),
    (dt.datetime(2026, 9, 11, 17, 30), dt.date(2026, 9, 11)),
    ("2026-09-11", dt.date(2026, 9, 11)),
    (" 2026-09-11 ", dt.date(2026, 9, 11)),
])
def test_a_date_is_taken_in_any_of_the_shapes_it_arrives_in(given, expect):
    """The fixture speaks ISO strings and fxrates speaks dates. Both sides
    have to agree case for case, so both shapes have to arrive at the same
    day -- including a datetime, where taking the date is not a no-op."""
    assert pounds._as_date(given) == expect


def test_a_whole_number_rate_has_no_decimal_places():
    """`_parts` splits a decimal into an integer and a scale. A rate with no
    point at all is the edge that has no fractional digits to move."""
    assert pounds._parts("20") == (20, 0)
    assert pounds._parts(Decimal("1.6064")) == (16064, 4)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_a_rate_that_is_not_a_number_is_refused(bad):
    """Decimal accepts these happily and they would poison every figure
    downstream, so they are caught where the rate enters."""
    with pytest.raises(money.MoneyError):
        pounds._parts(bad)


def test_dividing_rounds_half_away_from_zero_on_both_signs():
    """money.py's rounding, on integers. Half-up towards positive infinity
    would give -2 for the second of these, which is the disagreement that
    makes a refund column stop matching its own total."""
    assert pounds.div_half_up(5, 2) == 3
    assert pounds.div_half_up(-5, 2) == -3
    assert pounds.div_half_up(4, 2) == 2
    assert pounds.div_half_up(-1, 2) == -1


def test_dividing_by_nothing_is_refused():
    with pytest.raises(money.MoneyError):
        pounds.div_half_up(100, 0)
    with pytest.raises(money.MoneyError):
        pounds.div_half_up(100, -5)


def test_a_negative_markup_is_refused_in_both_directions():
    """A negative fee would turn CIBC's markup into a discount and quietly
    overstate what a thousand dollars buys."""
    with pytest.raises(money.MoneyError):
        pounds.to_pounds(100000, "1.6064", "0.85815", fee_bp=-1)
    with pytest.raises(money.MoneyError):
        pounds.from_pounds(50000, "1.6064", "0.85815", fee_bp=-1)


def test_a_zero_rate_is_refused_in_both_directions():
    with pytest.raises(money.MoneyError):
        pounds.to_pounds(100000, "0", "0.85815")
    with pytest.raises(money.MoneyError):
        pounds.to_pounds(100000, "1.6064", "0")
    with pytest.raises(money.MoneyError):
        pounds.from_pounds(50000, "0", "0.85815")


def test_converting_nothing_has_no_effective_rate():
    """Pence per cent is undefined on zero cents, and reporting it as zero
    would read as a rate of nought rather than as no answer."""
    got = pounds.to_pounds(0, "1.6064", "0.85815")
    assert got["pounds_minor"] == 0
    assert got["effective_rate"] is None


def test_a_published_rate_of_zero_is_refused_rather_than_crossed(tmp_path):
    """A zero in the file is bad data, not a rate. Crossing with it would
    divide by nothing a step later, where the message would be about
    arithmetic rather than about the file."""
    path = tmp_path / "fx_rates.csv"
    path.write_text("date,CAD,GBP\n2026-09-11,0,0.85815\n", encoding="utf-8")
    fxrates.reset()
    try:
        with pytest.raises(pounds.PoundsError) as raised:
            pounds.legs("2026-09-11", directory=str(tmp_path))
        assert "implausible" in str(raised.value)
    finally:
        fxrates.reset()


# ------------------------------------------------- the cross rate's honesty


def test_both_legs_must_come_from_one_day(tmp_path):
    """A file with a gap in one leg would otherwise produce a cross rate
    that was never quoted. The refusal is the feature."""
    path = tmp_path / "fx_rates.csv"
    path.write_text(
        "date,CAD,GBP\n"
        "2026-09-11,1.6064,\n"          # CAD published, GBP missing
        "2026-09-10,1.6100,0.85800\n",
        encoding="utf-8")
    fxrates.reset()
    try:
        with pytest.raises(pounds.PoundsError) as raised:
            pounds.legs("2026-09-11", directory=str(tmp_path))
        assert "both legs from one day" in str(raised.value)
    finally:
        fxrates.reset()


def test_a_currency_the_file_does_not_carry_is_refused(tmp_path):
    """`fxrates.rate` gates on what the loaded cache actually has rather
    than on a hard-coded list, so that the pound wallet's date,CAD,GBP file
    works. A typo still has to be refused outright rather than walking back
    ten days for a column that does not exist."""
    path = tmp_path / "fx_rates.csv"
    path.write_text("date,CAD,GBP\n2026-09-11,1.6064,0.85815\n",
                    encoding="utf-8")
    fxrates.reset()
    try:
        # The two columns the file carries resolve.
        assert pounds.legs("2026-09-11", directory=str(tmp_path))

        # USD is a currency the app converts to elsewhere, and a typo is not
        # a currency at all. Against this file neither has a column, and
        # both are refused the same way.
        for missing in ("USD", "GBPP"):
            with pytest.raises(fxrates.RateError) as raised:
                fxrates.rate(pounds._as_date("2026-09-11"), missing,
                             directory=str(tmp_path))
            assert "not a currency this app converts to" in str(raised.value)
    finally:
        fxrates.reset()
