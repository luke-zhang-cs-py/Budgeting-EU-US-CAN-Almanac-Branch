"""The tool that rewrites the published figures: tools/refresh_figures.py.

tests/test_published_figures.py says the page's numbers are stale; this is
the script that makes them right again. It runs the whole suite under
coverage, so these tests never let it: `subprocess.run` is replaced by a fake
that writes the JSON report or the collection listing a real run would, and
ROOT, PAGE and README point at a small project in tmp_path. The page and
README in the repository are never written.
"""
import io
import json
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import refresh_figures as refresh   # noqa: E402

RUNNING = "%d.%d" % sys.version_info[:2]

PAGE = """<script>
var MODULES = [
  { name: 'pkg/b.py', lines:   2, stmts:   5, missed:  0, does: 'the second' },
  { name: 'a.py',     lines:   3, stmts:   2, missed:  0, does: 'the first' }
];

var TESTS = [
  { file: 'test_a.py', n:  2, of: 'the first module' },
  { file: 'test_b.py', n:  1, of: 'the second one' }
];

var TEST_LINES = 4;
var MEASURED_WITH = 'Python %s';
</script>
""" % RUNNING

# What a run of the project above measures, when the page is right.
MEASURED = {"pkg/b.py": {"lines": 2, "stmts": 5, "missed": 0},
            "a.py": {"lines": 3, "stmts": 2, "missed": 0}}
COUNTS = {"test_a.py": 2, "test_b.py": 1}


def write(path, text):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with io.open(str(path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A tiny project: two modules, one test file, a page and a README."""
    write(tmp_path / "a.py", "x = 1\ny = 2\nz = 3\n")
    write(tmp_path / "pkg" / "b.py", "a = 1\nb = 2\n")
    write(tmp_path / "tests" / "test_a.py", "1\n2\n3\n4\n")
    # Neither of these is a test file's line.
    write(tmp_path / "tests" / "__pycache__" / "junk.py", "1\n" * 10)
    write(tmp_path / "tests" / "notes.txt", "1\n" * 10)
    write(tmp_path / "docs" / "index.html", PAGE)
    write(tmp_path / "README.md",
          "## Tests\n\n3 tests, 100% of 7 statements.\n")
    monkeypatch.setattr(refresh, "ROOT", str(tmp_path))
    monkeypatch.setattr(refresh, "PAGE", str(tmp_path / "docs" / "index.html"))
    monkeypatch.setattr(refresh, "README", str(tmp_path / "README.md"))
    return tmp_path


def replace_run(monkeypatch, behaviour):
    """subprocess.run, answering with `behaviour(args, kwargs)`."""
    calls = []

    def fake(args, **kwargs):
        calls.append((args, kwargs))
        return behaviour(args, kwargs)
    monkeypatch.setattr(refresh.subprocess, "run", fake)
    return calls


def listing(*lines):
    return lambda args, kwargs: types.SimpleNamespace(
        stdout="\n".join(lines) + "\n")


# --------------------------------------------------------- measuring

def test_measure_reads_the_coverage_report_a_real_run_writes(project,
                                                             monkeypatch):
    """The JSON report names files with the platform's separator; the page
    uses forward slashes. Line counts come from the files themselves."""
    seen = {}

    def run(args, kwargs):
        target = [a for a in args if a.startswith("--cov-report=json:")][0]
        seen["report"] = target.split(":", 1)[1]
        write(seen["report"], json.dumps({"files": {
            "a.py": {"summary": {"num_statements": 2, "missing_lines": 1}},
            os.path.join("pkg", "b.py"): {
                "summary": {"num_statements": 5, "missing_lines": 0}},
        }}))
        return types.SimpleNamespace(returncode=0)

    calls = replace_run(monkeypatch, run)
    got = refresh.measure()

    assert got == {"a.py": {"lines": 3, "stmts": 2, "missed": 1},
                   "pkg/b.py": {"lines": 2, "stmts": 5, "missed": 0}}
    args, kwargs = calls[0]
    assert args[:4] == [sys.executable, "-m", "pytest", "-q"]
    assert "--cov" in args
    assert kwargs["cwd"] == str(project)
    assert not os.path.exists(seen["report"]), "the report was left behind"


def test_collect_counts_tests_per_file_not_per_definition(project,
                                                          monkeypatch):
    replace_run(monkeypatch, listing(
        "tests/test_a.py::test_one",
        "tests/test_a.py::test_two[1]",
        "tests/test_a.py::test_two[2]",
        "tests/sub/test_b.py::test_three",
        "",
        "4 tests collected in 0.10s"))
    assert refresh.collect() == {"test_a.py": 3, "test_b.py": 1}


def test_collect_stops_when_collection_finds_nothing(project, monkeypatch):
    """A broken import collects nothing, and a page rewritten from that
    would list no tests at all."""
    replace_run(monkeypatch, listing("ERROR tests/test_a.py", "1 error"))
    with pytest.raises(SystemExit) as refused:
        refresh.collect()
    assert "collection produced nothing" in str(refused.value)
    assert "ERROR tests/test_a.py" in str(refused.value)


# --------------------------------------------------------- the module block

def test_a_page_that_is_already_right_is_left_exactly_as_it_is(project):
    changes = []
    assert refresh.rewrite(PAGE, MEASURED, COUNTS, changes) == PAGE
    assert changes == []


def test_a_changed_figure_is_rewritten_and_reported(project):
    measured = dict(MEASURED, **{"a.py": {"lines": 4, "stmts": 3,
                                          "missed": 1}})
    changes = []
    page = refresh.fix_modules(PAGE, measured, changes)
    assert ("  { name: 'a.py',     lines:   4, stmts:   3, missed:  1, "
            "does: 'the first' }") in page
    assert changes == ["%-24s %s -> %s" % ("a.py", (3, 2, 0), (4, 3, 1))]


def test_modules_are_ordered_by_size_and_padded_to_the_longest_name(project):
    measured = {"a.py": {"lines": 3, "stmts": 9, "missed": 0},
                "pkg/b.py": {"lines": 2, "stmts": 5, "missed": 0}}
    page = refresh.fix_modules(PAGE, measured, [])
    rows = page.split("var MODULES = [\n")[1].split("\n];")[0].splitlines()
    assert rows == [
        "  { name: 'a.py',     lines:   3, stmts:   9, missed:  0, "
        "does: 'the first' },",
        "  { name: 'pkg/b.py', lines:   2, stmts:   5, missed:  0, "
        "does: 'the second' }",
    ]


def test_a_module_with_no_description_stops_the_rewrite(project):
    """Only a person knows what a new module does."""
    measured = dict(MEASURED, **{"new.py": {"lines": 1, "stmts": 1,
                                            "missed": 0}})
    with pytest.raises(SystemExit) as refused:
        refresh.fix_modules(PAGE, measured, [])
    assert "no description on the page for new.py" in str(refused.value)


def test_a_module_coverage_no_longer_reports_is_flagged(project):
    measured = {"a.py": MEASURED["a.py"]}
    changes = []
    page = refresh.fix_modules(PAGE, measured, changes)
    assert "pkg/b.py" not in page
    assert changes == ["pkg/b.py is on the page but coverage no longer "
                       "reports it -- remove it, or check whether it became "
                       "omitted"]


# ----------------------------------------------------------- the test block

def test_a_changed_test_count_is_rewritten_and_reported(project):
    changes = []
    page = refresh.fix_tests(PAGE, {"test_a.py": 12, "test_b.py": 1}, changes)
    assert "  { file: 'test_a.py', n: 12, of: 'the first module' }," in page
    assert changes == ["%-28s %s -> %s" % ("test_a.py", 2, 12)]


def test_a_test_file_with_no_description_stops_the_rewrite(project):
    with pytest.raises(SystemExit) as refused:
        refresh.fix_tests(PAGE, dict(COUNTS, **{"test_c.py": 1}), [])
    assert "no description on the page for test_c.py" in str(refused.value)


def test_a_test_file_that_collects_nothing_is_flagged(project):
    changes = []
    page = refresh.fix_tests(PAGE, {"test_a.py": 2}, changes)
    assert "test_b.py" not in page
    assert changes == ["test_b.py is on the page but collects nothing"]


# ------------------------------------------------- the interpreter and lines

def test_the_interpreter_is_recorded(project):
    changes = []
    page = refresh.fix_measured_with(
        PAGE.replace("Python %s" % RUNNING, "Python 2.7"), changes)
    assert "var MEASURED_WITH = 'Python %s';" % RUNNING in page
    assert changes == ["MEASURED_WITH Python 2.7 -> %s" % RUNNING]


def test_a_page_that_does_not_say_which_python_stops_the_rewrite(project):
    with pytest.raises(SystemExit) as refused:
        refresh.fix_measured_with("var MODULES = [];", [])
    assert "no MEASURED_WITH line" in str(refused.value)


def test_test_lines_count_python_files_under_tests_only(project):
    changes = []
    page = refresh.fix_test_lines(PAGE.replace("TEST_LINES = 4",
                                               "TEST_LINES = 99"), changes)
    assert "var TEST_LINES = 4;" in page
    assert changes == ["TEST_LINES 99 -> 4"]


def test_a_page_without_a_test_line_count_is_left_alone(project):
    changes = []
    assert refresh.fix_test_lines("no figure here", changes) == \
        "no figure here"
    assert changes == []


# ---------------------------------------------------------------- the README

def test_a_stale_readme_sentence_is_rewritten(project):
    changes = []
    counts = {"test_a.py": 1200, "test_b.py": 34}
    measured = {"a.py": {"lines": 3, "stmts": 2000, "missed": 0}}
    refresh.fix_readme(measured, counts, changes)
    text = (project / "README.md").read_text(encoding="utf-8")
    assert "1,234 tests, 100% of 2,000 statements." in text
    assert changes == ["README  3 tests, 100% of 7 statements -> "
                       "1,234 tests, 100% of 2,000 statements"]


def test_a_readme_that_is_right_is_not_written(project, monkeypatch):
    written = []
    monkeypatch.setattr(refresh, "write", lambda *a: written.append(a))
    changes = []
    refresh.fix_readme(MEASURED, COUNTS, changes)
    assert changes == [] and written == []


def test_a_readme_that_states_no_figures_is_left_alone(project):
    write(project / "README.md", "# A project\n")
    changes = []
    refresh.fix_readme(MEASURED, {"test_a.py": 50}, changes)
    assert (project / "README.md").read_text(encoding="utf-8") == \
        "# A project\n"
    assert changes == []


def test_no_readme_is_not_an_error(project):
    os.remove(str(project / "README.md"))
    changes = []
    refresh.fix_readme(MEASURED, COUNTS, changes)
    assert changes == []


# ------------------------------------------------------------------- main()

def fake_measurements(monkeypatch, measured=MEASURED, counts=COUNTS):
    monkeypatch.setattr(refresh, "measure", lambda: measured)
    monkeypatch.setattr(refresh, "collect", lambda: counts)


def test_main_on_a_page_that_is_right_prints_only_the_totals(project,
                                                             monkeypatch,
                                                             capsys):
    fake_measurements(monkeypatch)
    written = []
    monkeypatch.setattr(refresh, "write", lambda *a: written.append(a))
    refresh.main()
    assert capsys.readouterr().out == "  3 tests, 7 statements, 100% covered\n"
    assert written == [], "a page that was right was written anyway"


def test_main_rewrites_the_page_and_says_what_changed(project, monkeypatch,
                                                      capsys):
    fake_measurements(monkeypatch, counts={"test_a.py": 5, "test_b.py": 1})
    refresh.main()
    page = (project / "docs" / "index.html").read_text(encoding="utf-8")
    assert "{ file: 'test_a.py', n:  5, of: 'the first module' }" in page
    readme = (project / "README.md").read_text(encoding="utf-8")
    assert "6 tests, 100% of 7 statements" in readme
    assert capsys.readouterr().out.splitlines() == [
        "  %-28s %s -> %s" % ("test_a.py", 2, 5),
        "  README  3 tests, 100% of 7 statements -> "
        "6 tests, 100% of 7 statements",
        "  6 tests, 7 statements, 100% covered",
    ]


def test_main_will_not_write_output_that_changes_on_a_second_pass(
        project, monkeypatch):
    """A tool that leaves a diff on every run is a tool nobody runs, so
    non-idempotent output is refused before anything is written."""
    fake_measurements(monkeypatch)
    passes = []

    def drifting(page, measured, counts, changes):
        passes.append(1)
        return page + " " * len(passes)
    monkeypatch.setattr(refresh, "rewrite", drifting)
    with pytest.raises(SystemExit) as refused:
        refresh.main()
    assert "not idempotent" in str(refused.value)
    assert (project / "docs" / "index.html").read_text(
        encoding="utf-8") == PAGE
