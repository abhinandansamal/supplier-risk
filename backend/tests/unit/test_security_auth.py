"""Tests for app.security.auth: demo users, password hashes, session tokens, login throttle."""

from collections.abc import Callable
from pathlib import Path

import jwt
import pytest
import yaml

from app.core.settings import Settings
from app.security.auth import (
    SESSION_SECONDS,
    InvalidSession,
    LoginThrottle,
    SessionTokens,
    UserDirectory,
    hash_password,
    verify_password,
)

SECRET = "jwt-secret-VALUE-8888-0123456789abcdef"
SUPPLIERS_YAML = Path(__file__).resolve().parents[3] / "data_pipeline" / "suppliers.yaml"


class Clock:
    """A settable clock."""

    def __init__(self, now: float = 1_800_000_000.0) -> None:
        """Start at a fixed time."""
        self.now = now

    def __call__(self) -> float:
        """Return the current time."""
        return self.now


# --- passwords -------------------------------------------------------------------------------


def test_password_hash_is_salted_and_verifies() -> None:
    first, second = hash_password("pw-1234567"), hash_password("pw-1234567")
    assert first != second
    assert verify_password("pw-1234567", first)
    assert not verify_password("pw-7654321", first)


def test_password_hash_never_contains_the_password() -> None:
    assert "pw-1234567" not in hash_password("pw-1234567")


# --- demo users ------------------------------------------------------------------------------


def test_directory_has_three_demo_users_with_fixed_roles(settings: Settings) -> None:
    directory = UserDirectory.from_settings(settings)
    users = {name: directory.get(name) for name in ("buyer", "risk_manager", "director")}
    assert {n: (u.role, u.category) for n, u in users.items() if u} == {
        "buyer": ("buyer", "electronics"),
        "risk_manager": ("risk_manager", None),
        "director": ("director", None),
    }


def test_buyer_category_exists_in_suppliers_yaml(settings: Settings) -> None:
    categories = {s["category"] for s in yaml.safe_load(SUPPLIERS_YAML.read_text())["suppliers"]}
    assert settings.demo_buyer_category in categories


def test_authenticate_accepts_only_the_right_password(settings: Settings) -> None:
    directory = UserDirectory.from_settings(settings)
    password = settings.demo_buyer_password
    assert password is not None
    user = directory.authenticate("buyer", password.get_secret_value())
    assert user is not None and user.context.role == "buyer"
    assert directory.authenticate("buyer", "wrong-password") is None
    assert directory.authenticate("nobody", "wrong-password") is None


def test_a_user_without_a_password_cannot_log_in(
    make_settings: Callable[..., Settings],
) -> None:
    directory = UserDirectory.from_settings(make_settings(demo_director_password=None))
    assert directory.get("director") is None
    assert directory.authenticate("director", "") is None


def test_directory_keeps_no_plain_password(settings: Settings) -> None:
    directory = UserDirectory.from_settings(settings)
    password = settings.demo_risk_password
    assert password is not None
    assert password.get_secret_value() not in repr(vars(directory))


# --- session tokens --------------------------------------------------------------------------


def test_session_lasts_30_minutes() -> None:
    clock = Clock()
    tokens = SessionTokens(SECRET, clock=clock)
    token, expires_at = tokens.issue("buyer")
    assert SESSION_SECONDS == 1800
    assert expires_at == int(clock.now) + 1800
    assert tokens.verify(token).subject == "buyer"
    clock.now += 1801
    with pytest.raises(InvalidSession):
        tokens.verify(token)


def test_a_token_signed_with_another_secret_is_refused() -> None:
    other = SessionTokens("another-secret-0123456789abcdef-xyz")
    token, _ = other.issue("director")
    with pytest.raises(InvalidSession):
        SessionTokens(SECRET).verify(token)


@pytest.mark.parametrize("algorithm", ["none", "HS512"])
def test_only_hs256_is_accepted(algorithm: str) -> None:
    claims = {"sub": "director", "exp": 4_000_000_000, "iat": 1, "jti": "x", "iss": "supplier-risk"}
    key = None if algorithm == "none" else SECRET
    token = jwt.encode({**claims, "aud": "supplier-risk-ui"}, key, algorithm=algorithm)
    with pytest.raises(InvalidSession):
        SessionTokens(SECRET).verify(token)


@pytest.mark.parametrize("missing", ["exp", "jti", "sub", "aud", "iss"])
def test_a_token_missing_a_claim_is_refused(missing: str) -> None:
    claims = {
        "sub": "director",
        "exp": 4_000_000_000,
        "iat": 1,
        "jti": "x",
        "iss": "supplier-risk",
        "aud": "supplier-risk-ui",
    }
    del claims[missing]
    with pytest.raises(InvalidSession):
        SessionTokens(SECRET).verify(jwt.encode(claims, SECRET, algorithm="HS256"))


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "x" * 5000])
def test_malformed_tokens_are_refused(token: str) -> None:
    with pytest.raises(InvalidSession):
        SessionTokens(SECRET).verify(token)


def test_a_revoked_token_is_refused() -> None:
    tokens = SessionTokens(SECRET)
    token, _ = tokens.issue("buyer")
    tokens.revoke(tokens.verify(token))
    with pytest.raises(InvalidSession):
        tokens.verify(token)


# --- login throttle --------------------------------------------------------------------------


def test_five_failures_lock_the_username_for_a_while() -> None:
    clock = Clock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(5):
        assert not throttle.locked("buyer")
        throttle.failed("buyer")
    assert throttle.locked("buyer")
    assert not throttle.locked("director")
    clock.now += 15 * 60 + 1
    assert not throttle.locked("buyer")


def test_a_success_clears_the_failures() -> None:
    throttle = LoginThrottle()
    for _ in range(4):
        throttle.failed("buyer")
    throttle.reset("buyer")
    throttle.failed("buyer")
    assert not throttle.locked("buyer")


def test_throttle_memory_is_bounded() -> None:
    throttle = LoginThrottle()
    for index in range(20_000):
        throttle.failed(f"user-{index}")
    assert len(throttle) <= 10_000
