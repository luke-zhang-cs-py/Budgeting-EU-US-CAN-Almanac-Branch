"""The tool that writes the pound wallet's rate file and its shared fixture.

`tools/publish_pound_rates.py` downloads the ECB history, keeps CAD and GBP,
writes `standalone/pounds/fx_rates.csv`, and regenerates the cases
tests/test_pounds.py holds the browser to. The download is replaced here by a
zip built in the test, in the ECB's own shape -- trailing comma, phantom
column, "N/A" for a currency that was not published -- so nothing touches the
network, and every output goes to tmp_path rather than standalone/pounds/.
"""
import datetime as dt
import io
import json
import os
import sys
import zipfile
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import publish_pound_rates as publish   # noqa: E402
from fx import fxrates                  # noqa: E402
from fx import pounds                   # noqa: E402

FIXTURE = os.path.join(ROOT, "standalone", "pounds", "cases.json")


@pytest.fixture(autouse=True)
def fresh_rates():
    fxrates.reset()
    yield
    fxrates.reset()


def ecb_zip(*rows):
    """The ECB history as it arrives: one CSV in a zip, a trailing comma on
    every line. Each row is (date, USD, CAD, GBP)."""
    lines = ["Date,USD,CAD,GBP,"]
    lines += [",".join(row) + "," for row in rows]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("eurofxref-hist.csv", "\n".join(lines) + "\n")
    return buffer.getvalue()


HISTORY = ecb_zip(
    ("2026-09-11", "1.1700", "1.6064", "0.85815"),
    ("2026-09-10", "1.1650", "1.6049", "0.85915"),
    # A day with one leg: kept, so the walk-back cannot quietly land on a
    # different date -- pounds.legs refuses it instead.
    ("2026-09-09", "1.1652", "1.6043", "N/A"),
)


def downloads(monkeypatch, payload):
    """fetch.get, answering `payload` and recording what it was asked."""
    asked = []

    def fake(url, timeout):
        asked.append((url, timeout))
        return payload
    monkeypatch.setattr(publish.fetch, "get", fake)
    return asked


# -------------------------------------------------------------- the rows

def test_only_the_two_legs_are_kept_and_a_missing_one_stays_missing(
        monkeypatch):
    asked = downloads(monkeypatch, HISTORY)
    rates = publish.build_rows(timeout=7)
    assert asked == [(fxrates.HISTORY_URL, 7)]
    assert rates == {
        dt.date(2026, 9, 11): {"CAD": Decimal("1.6064"),
                               "GBP": Decimal("0.85815")},
        dt.date(2026, 9, 10): {"CAD": Decimal("1.6049"),
                               "GBP": Decimal("0.85915")},
        dt.date(2026, 9, 9): {"CAD": Decimal("1.6043")},
    }


@pytest.mark.parametrize("payload", [None, b""])
def test_a_failed_download_stops_rather_than_writing_nothing(monkeypatch,
                                                             payload):
    downloads(monkeypatch, payload)
    with pytest.raises(SystemExit) as refused:
        publish.build_rows()
    assert str(refused.value) == "could not download the ECB history"


def test_a_history_with_no_rows_stops_too(monkeypatch):
    downloads(monkeypatch, ecb_zip())
    with pytest.raises(SystemExit) as refused:
        publish.build_rows()
    assert str(refused.value) == "the ECB history parsed to nothing"


def test_the_file_is_newest_first_with_a_blank_for_a_missing_leg(
        monkeypatch, tmp_path):
    downloads(monkeypatch, HISTORY)
    path = tmp_path / "fx_rates.csv"
    publish.write_csv(publish.build_rows(), str(path))
    assert path.read_bytes().decode("utf-8").splitlines() == [
        "date,CAD,GBP",
        "2026-09-11,1.6064,0.85815",
        "2026-09-10,1.6049,0.85915",
        "2026-09-09,1.6043,",
    ]


# ------------------------------------------------------------ the fixture

def test_the_committed_fixture_is_what_the_tool_makes_of_the_shipped_file(
        tmp_path):
    """The contract: run against the rate file it ships beside, the tool
    writes the fixture that is in the repository, byte for byte apart from
    the checkout's line endings."""
    out = tmp_path / "cases.json"
    count = publish.write_cases(str(out), publish.OUT)
    with io.open(FIXTURE, encoding="utf-8") as handle:
        committed = handle.read()
    assert out.read_bytes().decode("utf-8") == committed.replace("\r\n", "\n")
    assert count == len(json.loads(committed)["cases"])


def test_a_date_that_cannot_be_crossed_is_recorded_as_an_error(tmp_path):
    """Refusing is as much a rule as converting, so a refusal is a case the
    browser has to reproduce -- by name, not by message."""
    rates = {dt.date(2026, 9, 11): {"CAD": Decimal("1.6064"),
                                    "GBP": Decimal("0.85815")},
             dt.date(2026, 9, 9): {"CAD": Decimal("1.6043")}}
    publish.write_csv(rates, str(tmp_path / fxrates.CACHE_NAME))
    out = tmp_path / "cases.json"
    count = publish.write_cases(str(out), str(tmp_path))

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["limitMinor"] == pounds.LIMIT_MINOR
    assert payload["feeBp"] == pounds.CIBC_FEE_BP
    by_day = {}
    for case in payload["cases"]:
        by_day.setdefault(case["on"], []).append(case)
    assert count == len(payload["cases"])
    # Its own day: one case per amount, each carrying both legs.
    assert len(by_day["2026-09-11"]) == len(publish.CASE_AMOUNTS)
    assert by_day["2026-09-11"][0]["cadRate"] == "1.6064"
    assert by_day["2026-09-11"][0]["gbpRate"] == "0.85815"
    # A date long before anything in the file.
    assert by_day["1999-01-04"] == [{"on": "1999-01-04",
                                     "error": "RateError"}]


# ------------------------------------------------------------------ main()

def test_main_writes_the_rates_and_the_cases(monkeypatch, tmp_path, capsys):
    out = tmp_path / "pounds"                 # not there yet: main makes it
    monkeypatch.setattr(publish, "OUT", str(out))
    asked = downloads(monkeypatch, HISTORY)
    monkeypatch.setattr(sys, "argv",
                        ["publish_pound_rates.py", "--timeout", "9"])
    publish.main()

    assert asked == [(fxrates.HISTORY_URL, 9)]
    rows = (out / "fx_rates.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "date,CAD,GBP" and len(rows) == 4

    payload = json.loads((out / "cases.json").read_text(encoding="utf-8"))
    printed = capsys.readouterr().out.splitlines()
    assert printed == [
        "  %s" % (out / "fx_rates.csv"),
        "  3 days, 2026-09-09 to 2026-09-11, 2 with both legs",
        "  cases.json: %d cases" % len(payload["cases"]),
    ]
    # 2026-09-11 is in the download, so its cases are real conversions.
    assert any(case["on"] == "2026-09-11" and "poundsMinor" in case
               for case in payload["cases"])
