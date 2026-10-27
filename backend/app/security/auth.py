"""Demo login: seeded users with scrypt password hashes, HS256 session tokens, a login throttle.

Security: the token only names the user (sub); role and category always come from the
server-side directory, so a token can never widen its own scope. Passwords come from the
settings, are hashed once at startup and the plain values are not kept.
"""

import hashlib
import hmac
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from pydantic import SecretStr

from app.core.settings import Settings
from app.security.identity import Category, Role, UserContext

SESSION_SECONDS = 30 * 60
ISSUER = "supplier-risk"
AUDIENCE = "supplier-risk-ui"
MAX_TOKEN_CHARS = 2048
# scrypt cost: about 16 MB and tens of milliseconds per check.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
_MAX_TRACKED = 10_000

Clock = Callable[[], float]


class InvalidSession(Exception):  # noqa: N818
    """The session token is missing, malformed, expired, revoked or forged."""


def hash_password(password: str) -> str:
    """Return a salted scrypt hash as "scrypt$<salt hex>$<hash hex>".

    Args:
        password: The plain password.

    Returns:
        The encoded hash.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Return True when the password matches the encoded hash (constant-time compare)."""
    _, salt, expected = encoded.split("$")
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), expected)


# Checked for unknown users too, so a missing user takes as long as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


@dataclass(frozen=True)
class DemoUser:
    """A seeded demo user.

    Attributes:
        username: Login name, also the user id.
        role: App role.
        category: Row scope of a buyer.
        password_hash: scrypt hash.
    """

    username: str
    role: Role
    category: Category | None
    password_hash: str

    @property
    def context(self) -> UserContext:
        """Return the verified session identity of this user."""
        return UserContext(user_id=self.username, role=self.role, category=self.category)


class UserDirectory:
    """The demo users that have a password set."""

    def __init__(self, users: list[DemoUser]) -> None:
        """Index the users by name."""
        self._users = {u.username: u for u in users}

    @classmethod
    def from_settings(cls, settings: Settings) -> "UserDirectory":
        """Hash the configured demo passwords; a user without one cannot log in.

        Args:
            settings: Settings with DEMO_*_PASSWORD and DEMO_BUYER_CATEGORY.

        Returns:
            The directory.
        """
        seeds: list[tuple[str, Role, Category | None, SecretStr | None]] = [
            ("buyer", "buyer", settings.demo_buyer_category, settings.demo_buyer_password),
            ("risk_manager", "risk_manager", None, settings.demo_risk_password),
            ("director", "director", None, settings.demo_director_password),
        ]
        users = [
            DemoUser(name, role, category, hash_password(secret.get_secret_value()))
            for name, role, category, secret in seeds
            if secret is not None
        ]
        return cls(users)

    def get(self, username: str) -> DemoUser | None:
        """Return the user, or None."""
        return self._users.get(username)

    def authenticate(self, username: str, password: str) -> DemoUser | None:
        """Return the user when the password is right, else None (same cost either way)."""
        user = self._users.get(username)
        matched = verify_password(password, user.password_hash if user else _DUMMY_HASH)
        return user if user and matched else None


@dataclass(frozen=True)
class Session:
    """The verified claims of a session token.

    Attributes:
        subject: Username.
        token_id: jti, used to revoke the token at logout.
        expires_at: Expiry (Unix seconds).
    """

    subject: str
    token_id: str
    expires_at: int


class SessionTokens:
    """Issue, verify and revoke HS256 session tokens (30 minutes)."""

    def __init__(self, secret: str, *, clock: Clock = time.time) -> None:
        """Create the issuer.

        Args:
            secret: HMAC key (JWT_SECRET).
            clock: Wall clock in Unix seconds.
        """
        self._secret = secret
        self._clock = clock
        self._revoked: dict[str, int] = {}

    def __repr__(self) -> str:
        """Return a repr without the secret."""
        return "SessionTokens(secret=***)"

    def issue(self, username: str) -> tuple[str, int]:
        """Return a new token and its expiry (Unix seconds)."""
        now = int(self._clock())
        expires_at = now + SESSION_SECONDS
        claims = {
            "sub": username,
            "iat": now,
            "exp": expires_at,
            "jti": uuid.uuid4().hex,
            "iss": ISSUER,
            "aud": AUDIENCE,
        }
        return jwt.encode(claims, self._secret, algorithm="HS256"), expires_at

    def verify(self, token: str) -> Session:
        """Return the session of a valid token.

        Raises:
            InvalidSession: If the token is malformed, forged, expired or revoked.
        """
        if not token or len(token) > MAX_TOKEN_CHARS:
            raise InvalidSession
        try:
            claims = jwt.decode(
                token,
                self._secret,
                # Security: one fixed algorithm, so "none" or a key confusion cannot pass.
                algorithms=["HS256"],
                audience=AUDIENCE,
                issuer=ISSUER,
                # Times are checked below against the injectable clock.
                options={
                    "require": ["exp", "iat", "sub", "jti", "iss", "aud"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except jwt.PyJWTError:
            raise InvalidSession from None
        try:
            session = Session(str(claims["sub"]), str(claims["jti"]), int(claims["exp"]))
        except (TypeError, ValueError):
            raise InvalidSession from None
        if session.expires_at <= self._clock() or session.token_id in self._revoked:
            raise InvalidSession
        return session

    def revoke(self, session: Session) -> None:
        """Refuse this token from now on (kept until it would have expired anyway)."""
        now = self._clock()
        self._revoked = {jti: exp for jti, exp in self._revoked.items() if exp > now}
        if len(self._revoked) < _MAX_TRACKED:
            self._revoked[session.token_id] = session.expires_at


class LoginThrottle:
    """Lock a username for a while after repeated failed logins (in process)."""

    def __init__(
        self, *, max_failures: int = 5, window_seconds: float = 15 * 60, clock: Clock = time.time
    ) -> None:
        """Create the throttle.

        Args:
            max_failures: Failures within the window that lock the username.
            window_seconds: Length of the window and of the lock.
            clock: Wall clock in seconds.
        """
        self._max = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[str, list[float]] = {}

    def __len__(self) -> int:
        """Return how many usernames are tracked."""
        return len(self._failures)

    def _recent(self, username: str) -> list[float]:
        cutoff = self._clock() - self._window
        recent = [t for t in self._failures.get(username, []) if t > cutoff]
        if recent:
            self._failures[username] = recent
        else:
            self._failures.pop(username, None)
        return recent

    def locked(self, username: str) -> bool:
        """Return True while the username has too many recent failures."""
        return len(self._recent(username)) >= self._max

    def failed(self, username: str) -> None:
        """Record a failed login."""
        if username not in self._failures and len(self._failures) >= _MAX_TRACKED:
            # Security: bounded memory, but a locked entry is never dropped (SEC-101); when
            # every entry is locked, the new name is not tracked.
            unlocked = next((u for u in self._failures if not self.locked(u)), None)
            if unlocked is None:
                return
            self._failures.pop(unlocked)
        self._failures.setdefault(username, []).append(self._clock())
        self._failures[username] = self._failures[username][-self._max :]

    def reset(self, username: str) -> None:
        """Forget the failures after a successful login."""
        self._failures.pop(username, None)
