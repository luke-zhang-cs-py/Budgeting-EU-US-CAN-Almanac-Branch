"""Move the published wallet's rate snapshot forward.

    python tools/publish_rates.py

The page at docs/app/ has no server, so it ships its own copy of the ECB
history: a purchase is converted in the browser against
`docs/app/fx_rates.csv` -- the same filename fx/fxrates.py reads, so the test
suite can point at the shipped file rather than at a working cache that a
fresh clone does not have.
That file is a snapshot, and the page says which day it runs to — a purchase
after that date on a weekday is refused rather than converted at a stale
rate, which is the same refusal the server makes.

This copies the current cache over it and regenerates `docs/app/cases.json`,
the fixture `tests/test_static_wallet.py` holds both implementations to.
Regenerating both together matters: the cases quote real rates, so a fixture
written against yesterday's file would start failing the moment the file
moved.

Refresh the cache first if it is stale — the app does it on boot, or:

    python -c "import fxrates; fxrates.refresh()"
"""
import datetime as dt
import io
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import money   # noqa: E402
from core import paths   # noqa: E402
from fx import fxcost    # noqa: E402
from fx import fxrates   # noqa: E402

APP = os.path.join(ROOT, "docs", "app")
CASES = os.path.join(APP, "cases.json")
SHIPPED = os.path.join(APP, fxrates.CACHE_NAME)

# Each case is a way of being wrong rather than a sample. The dates are fixed
# so the fixture keeps covering the same hazards as the file moves on.
LOOKUPS = [
    ("2026-09-08", "an ordinary weekday: its own rate, no lag"),
    ("2026-09-06", "a Sunday: back to Friday"),
    ("2026-09-05", "a Saturday: back to Friday"),
    ("2026-04-05", "Easter Sunday 2026: the longest gap in the series"),
    ("2026-04-06", "Easter Monday 2026"),
    ("2025-12-26", "Boxing Day 2025"),
    ("2025-12-28", "the Sunday inside the Christmas gap"),
    ("2026-01-01", "New Year's Day"),
    ("1999-01-04", "the first day the ECB published"),
]

CONVERSIONS = [
    (5230, "2026-09-08", "the worked example: 52.30 EUR"),
    (5230, "2026-09-06", "the same amount on a Sunday"),
    (1, "2026-09-08", "one cent, where rounding shows"),
    (-5230, "2026-09-08", "a refund: half away from zero, not towards it"),
    (250, "2026-09-08", "a rounding boundary"),
    (100000000, "2026-09-08", "a million euro, for the integer range"),
]

ESTIMATES = [
    (5230, "1.6043", 250, "CIBC's published 2.5% on the worked example"),
    (5230, "1.6043", 0, "a card with no foreign fee"),
    (350, "1.6043", 250, "a coffee, where the fee rounds"),
    (100000, "1.6043", 199, "a flight at 1.99%"),
]

PARSES = [
    ("1.234,56", "the German convention"),
    ("1,234.56", "the Irish one"),
    ("1,234", "three digits and one separator: grouping, not a fraction"),
    ("3.50", "the ordinary case"),
    ("-52,30", "a refund"),
    ("€52.30", "with a symbol on it"),
    ("1.2345", "a longer fraction is truncated, not refused"),
    ("(12,34)", "an accounting negative"),
    ("1 234,56", "space grouping"),
    ("1.234.567,89", "two grouping marks and a decimal comma"),
    ("12.3", "a single decimal digit"),
    ("EUR 5", "a currency word in front of it"),
]


def newest_and_edges(directory):
    """The newest published day, and the dates either side of it.

    Derived rather than typed: the cases have to include a date past the
    newest rate on a weekday (refused) and on a weekend (allowed, walks
    back), and which dates those are moves every time the file does.
    """
    newest = fxrates.newest(directory)
    out = [(newest.isoformat(), "the newest rate in the shipped file")]
    saturday = newest + dt.timedelta(days=(5 - newest.weekday()) % 7 or 7)
    out.append((saturday.isoformat(),
                "a Saturday past the newest rate: allowed, walks back"))
    out.append(((saturday + dt.timedelta(days=1)).isoformat(),
                "a Sunday past the newest rate: allowed, walks back"))
    weekday = newest + dt.timedelta(days=1)
    while weekday.weekday() >= 5:
        weekday += dt.timedelta(days=1)
    out.append((weekday.isoformat(),
                "a weekday past the newest rate: refused"))
    return out


def build(directory):
    cases = {"lookups": [], "conversions": [], "estimates": [], "parses": []}

    for on, why in LOOKUPS + newest_and_edges(directory):
        day = dt.date.fromisoformat(on)
        entry = {"on": on, "why": why, "rates": {}}
        for currency in ("CAD", "USD", "EUR"):
            try:
                value, used = fxrates.rate(day, currency, directory=directory)
                entry["rates"][currency] = {"rate": str(value),
                                            "from": used.isoformat()}
            except fxrates.RateError:
                entry["rates"][currency] = {"error": "RateError"}
        cases["lookups"].append(entry)

    for cents, on, why in CONVERSIONS:
        day = dt.date.fromisoformat(on)
        entry = {"cents": cents, "on": on, "why": why, "out": {}}
        for currency in ("CAD", "USD"):
            minor, value, used = fxrates.convert(cents, day, currency,
                                                 directory=directory)
            entry["out"][currency] = {"minor": minor, "rate": str(value),
                                      "rateDate": used.isoformat()}
        cases["conversions"].append(entry)

    for base, rate_text, fee_bp, why in ESTIMATES:
        got = fxcost.estimate(base, rate_text, fee_bp)
        cases["estimates"].append({
            "base": base, "rate": rate_text, "feeBp": fee_bp, "why": why,
            "converted": got["converted_minor"], "fee": got["fee_minor"],
            "total": got["total_minor"], "effective": got["effective_rate"]})

    for text, why in PARSES:
        try:
            minor = money.parse(text)
        except money.MoneyError:
            minor = None
        cases["parses"].append({"text": text, "why": why, "minor": minor})

    return cases


def main():
    directory = paths.data_dir()
    source = fxrates.cache_path(directory)
    if not os.path.exists(source):
        raise SystemExit(
            "no rate cache at %s. Run the app once, or:\n"
            "    python -c \"import fxrates; fxrates.refresh()\"" % source)

    was = None
    if os.path.exists(SHIPPED):
        with io.open(SHIPPED, encoding="utf-8") as handle:
            was = len(handle.read().splitlines())

    shutil.copyfile(source, SHIPPED)
    with io.open(SHIPPED, encoding="utf-8") as handle:
        now = len(handle.read().splitlines())

    cases = build(directory)
    io.open(CASES, "w", encoding="utf-8", newline="\n").write(
        json.dumps(cases, indent=1, ensure_ascii=False))

    print("  %s  %s-> %d rows, newest %s"
          % (fxrates.CACHE_NAME, "%d " % was if was else "", now,
             fxrates.newest(directory)))
    print("  cases.json %d lookups, %d conversions, %d estimates, %d parses"
          % (len(cases["lookups"]), len(cases["conversions"]),
             len(cases["estimates"]), len(cases["parses"])))
    print("  now run: pytest tests/test_static_wallet.py")


if __name__ == "__main__":
    main()
