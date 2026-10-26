"""The tool that moves the published wallet's rate snapshot forward.

`tools/publish_rates.py` copies the working rate cache over
`docs/app/fx_rates.csv` and regenerates `docs/app/cases.json`, the fixture
tests/test_static_wallet.py holds the Python and browser sides to. It is run
by hand, rarely, which is exactly when an untested script turns out to have
rotted.

Nothing here writes into docs/app/. The cache is a copy of the shipped file
in tmp_path, and the two outputs are pointed into tmp_path too.
"""
import datetime as dt
import io
import json
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import publish_rates as publish   # noqa: E402
from fx import fxrates            # noqa: E402

SHIPPED = os.path.join(ROOT, "docs", "app", "fx_rates.csv")
FIXTURE = os.path.join(ROOT, "docs", "app", "cases.json")


@pytest.fixture(autouse=True)
def fresh_rates():
    fxrates.reset()
    yield
    fxrates.reset()


@pytest.fixture
def published(tmp_path, monkeypatch):
    """A data directory holding a cache, and both outputs aimed at tmp_path."""
    data = tmp_path / "data"
    data.mkdir()
    shutil.copyfile(SHIPPED, str(data / fxrates.CACHE_NAME))
    monkeypatch.setenv("WALLET_DATA", str(data))

    out = tmp_path / "app"
    out.mkdir()
    monkeypatch.setattr(publish, "SHIPPED", str(out / fxrates.CACHE_NAME))
    monkeypatch.setattr(publish, "CASES", str(out / "cases.json"))
    return data, out


def write_rates(folder, *days):
    """A small cache, newest first, as the real one is written."""
    path = os.path.join(str(folder), fxrates.CACHE_NAME)
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("date,CAD,USD\n")
        for day in days:
            handle.write("%s,1.6000,1.1600\n" % day)
    return path


# ------------------------------------------------------------ the fixture

def test_the_committed_fixture_is_what_build_makes_of_the_shipped_file():
    """The contract: run against the file it ships beside, the tool writes
    the fixture that is in the repository. If this fails, either the rules
    moved and the fixture was not regenerated, or the tool drifted."""
    with io.open(FIXTURE, encoding="utf-8") as handle:
        committed = json.load(handle)
    assert publish.build(os.path.dirname(SHIPPED)) == committed


def test_the_edge_dates_follow_the_newest_rate_when_it_is_a_friday(tmp_path):
    """A Friday is the case where the next weekday is not the next day: the
    walk forward has to skip the whole weekend to find one to refuse."""
    write_rates(tmp_path, "2026-09-11", "2026-09-10")
    assert dt.date(2026, 9, 11).weekday() == 4
    edges = publish.newest_and_edges(str(tmp_path))
    assert [on for on, _ in edges] == [
        "2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14"]
    assert edges[-1][1] == "a weekday past the newest rate: refused"


def test_the_edge_dates_follow_the_newest_rate_midweek(tmp_path):
    write_rates(tmp_path, "2026-09-09", "2026-09-08")
    edges = publish.newest_and_edges(str(tmp_path))
    assert [on for on, _ in edges] == [
        "2026-09-09", "2026-09-12", "2026-09-13", "2026-09-10"]


def test_a_date_with_no_rate_is_recorded_as_a_refusal(tmp_path):
    """The fixture holds refusals as well as answers -- refusing is a rule
    the browser has to agree with too."""
    write_rates(tmp_path, "2026-09-08", "2026-09-04")
    cases = publish.build(str(tmp_path))
    first = [case for case in cases["lookups"] if case["on"] == "1999-01-04"]
    assert first[0]["rates"]["CAD"] == {"error": "RateError"}
    assert first[0]["rates"]["EUR"] == {"rate": "1", "from": "1999-01-04"}


def test_a_parse_that_money_refuses_is_recorded_as_none(tmp_path,
                                                        monkeypatch):
    write_rates(tmp_path, "2026-09-08", "2026-09-04")
    monkeypatch.setattr(publish, "PARSES",
                        [("twelve euro", "words"), ("3.50", "a number")])
    cases = publish.build(str(tmp_path))
    assert cases["parses"] == [
        {"text": "twelve euro", "why": "words", "minor": None},
        {"text": "3.50", "why": "a number", "minor": 350}]


# ------------------------------------------------------------------ main()

def test_main_copies_the_cache_and_writes_the_fixture(published, capsys):
    data, out = published
    publish.main()

    copied = out / fxrates.CACHE_NAME
    with io.open(SHIPPED, "rb") as handle:
        assert copied.read_bytes() == handle.read(), (
            "the snapshot is not a copy of the cache")
    with io.open(str(out / "cases.json"), encoding="utf-8") as handle:
        written = json.load(handle)
    with io.open(FIXTURE, encoding="utf-8") as handle:
        assert written == json.load(handle)

    printed = capsys.readouterr().out.splitlines()
    with io.open(SHIPPED, encoding="utf-8") as handle:
        rows = len(handle.read().splitlines())
    newest = fxrates.newest(str(data))
    assert printed[0] == "  %s  -> %d rows, newest %s" % (
        fxrates.CACHE_NAME, rows, newest)
    assert printed[1] == (
        "  cases.json %d lookups, %d conversions, %d estimates, %d parses"
        % (len(written["lookups"]), len(written["conversions"]),
           len(written["estimates"]), len(written["parses"])))
    assert printed[2] == "  now run: pytest tests/test_static_wallet.py"


def test_main_says_how_many_rows_the_old_snapshot_had(published, capsys):
    """Moving the snapshot forward should show by how much."""
    _data, out = published
    write_rates(out, "2026-01-02", "2025-12-31")      # three lines
    publish.main()
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("  %s  3 -> " % fxrates.CACHE_NAME), first


def test_main_with_no_cache_says_how_to_make_one(tmp_path, monkeypatch):
    monkeypatch.setenv("WALLET_DATA", str(tmp_path))
    monkeypatch.setattr(publish, "SHIPPED", str(tmp_path / "shipped.csv"))
    with pytest.raises(SystemExit) as refused:
        publish.main()
    message = str(refused.value)
    assert fxrates.cache_path(str(tmp_path)) in message
    # The command it suggests has to be one that runs from the root.
    assert "from fx import fxrates; fxrates.refresh()" in message
    assert not (tmp_path / "shipped.csv").exists()
