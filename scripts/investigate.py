"""Run one investigation from the command line and print the report and the agent trace.

Usage:
    uv run python scripts/investigate.py --role risk_manager "Why is Microchip (S0003) risky?"
    uv run python scripts/investigate.py --role buyer --category electronics "Why is S0003 late?"

Uses the real services from .env: Zetaris (role key of --role), Meterless Nightshift (at most
INVESTIGATION_CALL_BUDGET model calls) and the context service (case board; the run goes on
without it). The snapshot cache lives in this process, so the views are warmed first (minutes
for SEC/Eurostat); --no-warm skips that and cold views then wait at most
TOOL_CALL_TIMEOUT_SECONDS. Writes the trace to runs/<investigation_id>.jsonl and, with
--json-out, the whole result as JSON. Never prints keys, prompts or model output other than
the verified report.

Exit codes: 0 completed, 1 incomplete or escalated, 2 rejected or settings missing.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# The backend is not an installed package; make it importable when run as a script.
_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT / "backend"), str(_ROOT)]

from app.agent.graph import Investigator  # noqa: E402
from app.agent.models import InvestigationResult, TraceEvent  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.core.settings import SettingsError, load_settings  # noqa: E402
from app.llm.client import MeterlessClient  # noqa: E402
from app.memory.world_model import WorldModelClient  # noqa: E402
from app.security.identity import UserContext  # noqa: E402
from app.tools.cache import SnapshotCache  # noqa: E402

ROLES = ("buyer", "risk_manager", "director")
CATEGORIES = ("electronics", "chemicals", "metals", "logistics")
EXIT_CODES = {"completed": 0, "incomplete": 1, "escalated": 1, "rejected": 2}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line before any work, so --help only prints usage.

    Args:
        argv: Command line arguments, or None to use sys.argv.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("question", help="the question (at most 1,000 characters)")
    parser.add_argument("--role", choices=ROLES, default="risk_manager")
    parser.add_argument("--category", choices=CATEGORIES, help="row scope of a buyer")
    parser.add_argument("--user-id", default="cli-user")
    parser.add_argument("--json-out", type=Path, help="write the whole result as JSON here")
    parser.add_argument("--trace-dir", type=Path, default=Path("runs"))
    parser.add_argument("--no-warm", action="store_true", help="do not warm the views first")
    return parser.parse_args(argv)


def user_from_args(args: argparse.Namespace) -> UserContext:
    """Return the session identity of the command line (a stand-in for the login).

    Raises:
        SystemExit: If the identity is invalid (a buyer needs a category).
    """
    try:
        return UserContext(user_id=args.user_id, role=args.role, category=args.category)
    except ValueError:
        raise SystemExit("invalid session: a buyer needs --category") from None


def _report_lines(result: InvestigationResult) -> list[str]:
    """Return the report part: headline, numbered claims, missing, notes, proposals."""
    lines: list[str] = []
    report = result.report
    if report is not None:
        lines += ["", report.headline, ""]
        for number, claim in enumerate(report.claims, start=1):
            flag = "" if claim.verified else " [UNVERIFIED: " + "; ".join(claim.problems) + "]"
            kind = " (hypothesis)" if claim.kind == "hypothesis" else ""
            ids = ", ".join(claim.evidence_ids)
            lines.append(f"[{number}] {claim.text}{kind} {{{ids}}}{flag}")
        if report.missing:
            lines += ["", "Missing:", *(f"- {item}" for item in report.missing)]
        if report.notes:
            lines += ["", "Notes:", *(f"- {item}" for item in report.notes)]
    if result.proposals:
        lines += ["", "Proposals (pending approval):"]
        lines += [
            f"- {p.kind} for {p.supplier_id}, impact {p.impact} {{{', '.join(p.evidence_ids)}}}"
            for p in result.proposals
        ]
    return lines


def _trace_line(event: TraceEvent) -> str:
    """Return one trace event as a line (ids, counts and codes only)."""
    fields = {
        "step": event.step,
        "model": event.model_requested,
        "receipt": event.receipt_id,
        "tokens": event.tokens,
        "ms": event.latency_ms,
        "tools": ",".join(event.tools) or None,
        "verdict": event.verdict,
        "note": event.note,
    }
    shown = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    return f"{event.seq:>3} {event.agent}/{event.action} {shown}".rstrip()


def render(result: InvestigationResult) -> str:
    """Return the report and the trace as plain text."""
    lines = [f"Investigation {result.investigation_id}: {result.status}"]
    if result.reason:
        lines.append(f"Reason: {result.reason}. {result.message or ''}".rstrip())
    lines += _report_lines(result)
    lines += ["", f"Trace ({result.model_calls} model calls, {result.duration_ms} ms):"]
    lines += [_trace_line(event) for event in result.trace]
    return "\n".join(lines)


def write_json(result: InvestigationResult, path: Path) -> None:
    """Write the whole result as JSON (creates the folder)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


async def main(args: argparse.Namespace) -> int:
    """Run the investigation with the real services and print it.

    Args:
        args: Parsed command line.

    Returns:
        The exit code.
    """
    user = user_from_args(args)
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"FAIL settings: {exc}")
        return 2
    setup_logging(settings)
    cache = SnapshotCache(settings)
    llm = MeterlessClient(settings)
    board = WorldModelClient(settings)
    try:
        if not args.no_warm:
            await cache.warm_all()
        investigator = Investigator(
            settings, llm=llm, cache=cache, board_client=board, trace_dir=args.trace_dir
        )
        result = await investigator.run_investigation(user, args.question)
    finally:
        await cache.aclose()
        await llm.aclose()
        await board.aclose()
    print(render(result))
    if args.json_out:
        write_json(result, args.json_out)
    return EXIT_CODES[result.status]


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
