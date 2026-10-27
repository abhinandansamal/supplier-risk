"""docker-compose files and the context-service image: static checks (no Docker needed)."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return _load("docker-compose.yml")


@pytest.fixture(scope="module")
def service(compose: dict[str, Any]) -> dict[str, Any]:
    return compose["services"]["context-service"]


def test_has_no_host_port(service: dict[str, Any]):
    assert "ports" not in service


def test_is_only_on_the_internal_network(compose: dict[str, Any], service: dict[str, Any]):
    assert list(service["networks"]) == ["context_net"]
    assert compose["networks"]["context_net"]["internal"] is True


def test_data_lives_in_a_named_volume(compose: dict[str, Any], service: dict[str, Any]):
    assert service["volumes"] == ["context_data:/data"]
    assert "context_data" in compose["volumes"]


def test_has_a_healthcheck_on_health(service: dict[str, Any]):
    assert "/health" in " ".join(service["healthcheck"]["test"])


def test_has_memory_and_cpu_limits(service: dict[str, Any]):
    assert service["mem_limit"] == "256m"
    assert 0 < float(service["cpus"]) <= 1


def test_runs_hardened(service: dict[str, Any]):
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]


def test_token_comes_from_env_and_is_required(service: dict[str, Any]):
    assert service["environment"]["CONTEXT_SERVICE_TOKEN"].startswith("${CONTEXT_SERVICE_TOKEN:?")


def test_dev_override_publishes_localhost_only():
    dev = _load("docker-compose.dev.yml")["services"]["context-service"]
    assert dev["ports"] == ["127.0.0.1:7070:7070"]
    # The dev network must reach the host, so it is a second, non-internal network.
    assert "context_dev" in dev["networks"]


def test_env_example_has_an_empty_token_placeholder():
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "CONTEXT_SERVICE_TOKEN=" in lines


def test_image_is_pinned_by_digest_and_runs_as_non_root():
    dockerfile = (ROOT / "context-service" / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"node:24-alpine@sha256:[0-9a-f]{64}", dockerfile)
    assert re.search(r"^USER node$", dockerfile, re.M)
    assert "--max-old-space-size=" in dockerfile


def test_has_a_process_limit_and_a_tolerant_healthcheck(service: dict[str, Any]):
    """Security: SEC-086. Synchronous store writes can block /health for a few seconds."""
    assert 0 < int(service["pids_limit"]) <= 256
    assert service["healthcheck"]["timeout"] == "5s"


def test_runtime_image_ships_no_package_manager():
    """Security: SEC-086."""
    dockerfile = (ROOT / "context-service" / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.split("AS runtime", 1)[1]
    assert re.search(r"rm -rf [^\n]*/usr/local/lib/node_modules/npm", runtime)
    assert "corepack" in runtime
