"""Turn a JUnit XML file into a Markdown test report.

Usage:
    python scripts/test_report.py reports/junit.xml --title "Unit tests (CI)"

The report is printed to stdout. The script always exits with code 0, so a reporting
problem never hides the real test result.
"""

import argparse
import math
import sys
import xml.etree.ElementTree as ET  # noqa: S405  # input is our own pytest output
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TITLE = "Test report"
MAX_CELL_LENGTH = 200
_MARKDOWN_SPECIALS = "\\|<>[]!`&"


@dataclass(frozen=True)
class TestCase:
    """One test case from a JUnit file.

    Attributes:
        test_id: "classname::name" of the test.
        outcome: One of passed, failed, error, skipped, xfailed.
        seconds: Run time in seconds.
        detail: First line of the failure or error text, empty otherwise.
    """

    __test__ = False  # not a pytest test class

    test_id: str
    outcome: str
    seconds: float
    detail: str = ""


@dataclass(frozen=True)
class Report:
    """Parsed JUnit results with totals per outcome.

    Attributes:
        cases: All test cases in file order.
    """

    cases: list[TestCase] = field(default_factory=list)

    def _count(self, outcome: str) -> int:
        return sum(1 for case in self.cases if case.outcome == outcome)

    @property
    def total(self) -> int:
        """Number of test cases."""
        return len(self.cases)

    @property
    def passed(self) -> int:
        """Number of passed tests."""
        return self._count("passed")

    @property
    def failed(self) -> int:
        """Number of failed tests."""
        return self._count("failed")

    @property
    def errors(self) -> int:
        """Number of tests with an error."""
        return self._count("error")

    @property
    def skipped(self) -> int:
        """Number of skipped tests."""
        return self._count("skipped")

    @property
    def xfailed(self) -> int:
        """Number of expected failures."""
        return self._count("xfailed")

    @property
    def duration(self) -> float:
        """Sum of test times in seconds."""
        return sum(case.seconds for case in self.cases)


def _first_line(element: ET.Element) -> str:
    """Returns the first non-empty line of an element's text or message."""
    for source in (element.text or "", element.get("message") or ""):
        for line in source.splitlines():
            if line.strip():
                return line.strip()
    return ""


def _classify(node: ET.Element) -> tuple[str, str]:
    """Returns (outcome, detail) for a testcase element."""
    failure = node.find("failure")
    if failure is not None:
        return "failed", _first_line(failure)
    error = node.find("error")
    if error is not None:
        return "error", _first_line(error)
    skipped = node.find("skipped")
    if skipped is not None:
        if "xfail" in (skipped.get("type") or ""):
            return "xfailed", ""
        return "skipped", ""
    return "passed", ""


def _parse_seconds(raw: str | None) -> float:
    """Returns a test time in seconds, or 0.0 if the value is missing or unusable."""
    try:
        value = float(raw or 0)
    except ValueError:
        return 0.0
    return value if math.isfinite(value) and value >= 0 else 0.0


def parse_junit(path: Path) -> Report:
    """Reads a JUnit XML file.

    Args:
        path: Path to the JUnit XML file.

    Returns:
        The parsed report.

    Raises:
        OSError: If the file cannot be read.
        ET.ParseError: If the file is not valid XML.
    """
    root = ET.parse(path).getroot()  # noqa: S314  # input is our own pytest output
    cases = []
    for node in root.iter("testcase"):
        outcome, detail = _classify(node)
        cases.append(
            TestCase(
                test_id=f"{node.get('classname', '')}::{node.get('name', '')}",
                outcome=outcome,
                seconds=_parse_seconds(node.get("time")),
                detail=detail,
            )
        )
    return Report(cases=cases)


def _cell(text: str) -> str:
    """Makes untrusted text safe for one Markdown table cell.

    Collapses whitespace (so no newline can end the row), truncates long text and
    escapes Markdown and HTML special characters.

    Args:
        text: Text from the JUnit file.

    Returns:
        Single-line text that renders literally.
    """
    flat = " ".join(text.split())
    if len(flat) > MAX_CELL_LENGTH:
        flat = flat[: MAX_CELL_LENGTH - 3] + "..."
    return "".join(f"\\{ch}" if ch in _MARKDOWN_SPECIALS else ch for ch in flat)


def render_markdown(report: Report, title: str) -> str:
    """Renders a report as Markdown.

    Args:
        report: The parsed report.
        title: Heading for the report.

    Returns:
        Markdown with a totals table and one row per test case.
    """
    lines = [
        f"## {title}",
        "",
        "| Tests | Passed | Failed | Errors | Skipped | XFailed | Duration |",
        "|---|---|---|---|---|---|---|",
        f"| {report.total} | {report.passed} | {report.failed} | {report.errors} "
        f"| {report.skipped} | {report.xfailed} | {report.duration:.2f}s |",
        "",
        "| Test | Outcome | Seconds | Failure |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {_cell(c.test_id)} | {c.outcome} | {c.seconds:.2f} | {_cell(c.detail)} |"
        for c in report.cases
    )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the command line tool.

    Args:
        argv: Command line arguments, or None to use sys.argv.

    Returns:
        Always 0.
    """
    parser = argparse.ArgumentParser(description="Print a JUnit XML file as Markdown.")
    parser.add_argument("xml_path", type=Path, help="path to the JUnit XML file")
    parser.add_argument("--title", default=DEFAULT_TITLE, help="report heading")
    args = parser.parse_args(argv)
    try:
        report = parse_junit(args.xml_path)
    except (OSError, ET.ParseError) as exc:
        sys.stdout.write(f"## {args.title}\n\nCould not read {args.xml_path}: {exc}\n")
        return 0
    sys.stdout.write(render_markdown(report, args.title))
    return 0


if __name__ == "__main__":
    sys.exit(main())
