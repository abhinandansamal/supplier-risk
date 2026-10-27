"""Every script in scripts/ answers --help with usage and exits 0 without doing any work."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = sorted(p for p in (ROOT / "scripts").iterdir() if p.suffix in {".py", ".sh"})
# Settings that would let a script reach a real service; --help must never need them.
SERVICE_VARIABLES = ("ZETARIS", "METERLESS", "ERP_", "APP_DATABASE", "CONTEXT_SERVICE")


def _clean_env(tmp_path: Path) -> dict[str, str]:
    """Return an environment without service settings, run from an empty directory."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(SERVICE_VARIABLES)}
    env["HOME"] = str(tmp_path)
    return env


def test_every_script_is_covered() -> None:
    names = {p.name for p in SCRIPTS}
    assert {"warm_cache.py", "check_llm.py", "erp_up.sh", "investigate.py"} <= names


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_help_prints_usage_and_does_no_work(script: Path, tmp_path: Path) -> None:
    command = ["bash", str(script)] if script.suffix == ".sh" else [sys.executable, str(script)]
    # cwd is an empty folder: a script that started work would find no .env and print FAIL/SKIP.
    done = subprocess.run(  # noqa: S603 - fixed local scripts, no shell
        [*command, "--help"],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.lower().startswith("usage:")
    # Status lines (what a script prints once it works) must not appear.
    assert not [
        line for line in done.stdout.splitlines() if line.startswith(("FAIL ", "SKIP ", "PASS "))
    ]
    assert list(tmp_path.iterdir()) == [], "--help wrote files"
