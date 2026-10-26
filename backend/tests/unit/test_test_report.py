"""Tests for scripts/test_report.py (JUnit XML to Markdown)."""

from pathlib import Path

import pytest

from scripts import test_report

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="5" time="1.5">
    <testcase classname="t.test_a" name="test_ok" time="0.10"/>
    <testcase classname="t.test_a" name="test_bad" time="0.20">
      <failure message="assert 1 == 2">assert 1 == 2
second line</failure>
    </testcase>
    <testcase classname="t.test_a" name="test_err" time="0.30">
      <error message="boom">RuntimeError: boom
trace</error>
    </testcase>
    <testcase classname="t.test_a" name="test_skip" time="0.00">
      <skipped type="pytest.skip" message="not today"/>
    </testcase>
    <testcase classname="t.test_a" name="test_xfail" time="0.00">
      <skipped type="pytest.xfail" message="known bug"/>
    </testcase>
  </testsuite>
</testsuites>
"""


@pytest.fixture
def xml_file(tmp_path: Path) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT, encoding="utf-8")
    return path


def test_parse_counts_each_outcome(xml_file: Path) -> None:
    report = test_report.parse_junit(xml_file)
    assert report.total == 5
    assert report.passed == 1
    assert report.failed == 1
    assert report.errors == 1
    assert report.skipped == 1
    assert report.xfailed == 1
    assert report.duration == pytest.approx(0.6)


def test_parse_cases_have_id_outcome_and_failure_line(xml_file: Path) -> None:
    cases = {c.test_id: c for c in test_report.parse_junit(xml_file).cases}
    assert cases["t.test_a::test_ok"].outcome == "passed"
    assert cases["t.test_a::test_bad"].outcome == "failed"
    assert cases["t.test_a::test_bad"].detail == "assert 1 == 2"
    assert cases["t.test_a::test_err"].detail == "RuntimeError: boom"
    assert cases["t.test_a::test_ok"].detail == ""


def test_render_markdown_has_title_totals_and_rows(xml_file: Path) -> None:
    text = test_report.render_markdown(test_report.parse_junit(xml_file), "My title")
    assert text.startswith("## My title")
    assert "| Tests | Passed | Failed | Errors | Skipped | XFailed | Duration |" in text
    assert "| t.test_a::test_bad | failed | 0.20 | assert 1 == 2 |" in text
    assert "| t.test_a::test_ok | passed | 0.10 |  |" in text


def test_render_escapes_pipes_in_details(tmp_path: Path) -> None:
    path = tmp_path / "j.xml"
    path.write_text(
        '<testsuite><testcase classname="a" name="b" time="0">'
        '<failure message="x">a | b</failure></testcase></testsuite>',
        encoding="utf-8",
    )
    text = test_report.render_markdown(test_report.parse_junit(path), "T")
    assert "a \\| b" in text


def test_main_prints_markdown_and_returns_zero(
    xml_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert test_report.main([str(xml_file), "--title", "CI"]) == 0
    assert "## CI" in capsys.readouterr().out


def test_main_default_title(xml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert test_report.main([str(xml_file)]) == 0
    assert "## Test report" in capsys.readouterr().out


def test_main_missing_file_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert test_report.main([str(tmp_path / "nope.xml")]) == 0
    assert "could not read" in capsys.readouterr().out.lower()


def test_main_invalid_xml_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_text("<not xml", encoding="utf-8")
    assert test_report.main([str(bad)]) == 0
    assert "could not read" in capsys.readouterr().out.lower()


def _junit_with(tmp_path: Path, testcase: str) -> Path:
    path = tmp_path / "hostile.xml"
    path.write_text(f"<testsuite>{testcase}</testsuite>", encoding="utf-8")
    return path


def test_report_escapes_markdown_newlines_and_html(tmp_path: Path) -> None:
    """SEC-001: hostile ids and failure text must not break out of the table."""
    path = _junit_with(
        tmp_path,
        '<testcase classname="a&#10;# H1 ![i](http://evil/x.png)" name="b&#10;&lt;b&gt;x" time="0">'
        '<failure message="m">line &lt;script&gt;&#10;[link](http://evil)</failure></testcase>',
    )
    text = test_report.render_markdown(test_report.parse_junit(path), "T")
    for line in text.splitlines():
        assert line == "" or line.startswith(("|", "## T"))
    assert "![" not in text
    assert "<b>" not in text
    assert "<script>" not in text
    assert "[link]" not in text


def test_report_truncates_long_cells(tmp_path: Path) -> None:
    """SEC-001: cells are capped so one test cannot flood the summary."""
    path = _junit_with(
        tmp_path,
        f'<testcase classname="a" name="{"x" * 1000}" time="0"/>',
    )
    text = test_report.render_markdown(test_report.parse_junit(path), "T")
    assert "x" * 300 not in text
    assert "..." in text


@pytest.mark.parametrize("time_value", ["abc", "nan", "inf", "-1", ""])
def test_parse_bad_time_defaults_to_zero(tmp_path: Path, time_value: str) -> None:
    """SEC-002: an unusable time attribute must not crash the parser."""
    path = _junit_with(tmp_path, f'<testcase classname="a" name="b" time="{time_value}"/>')
    report = test_report.parse_junit(path)
    assert report.cases[0].seconds == 0.0
    assert report.duration == 0.0


def test_main_bad_time_exits_zero_and_prints_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SEC-002: the 'always exits 0' contract holds for bad time values."""
    path = _junit_with(tmp_path, '<testcase classname="a" name="b" time="oops"/>')
    assert test_report.main([str(path)]) == 0
    assert "| a::b | passed | 0.00 |" in capsys.readouterr().out
