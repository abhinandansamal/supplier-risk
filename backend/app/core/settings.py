"""Application settings loaded from the environment (and .env) with pydantic-settings.

All configuration lives here. Secrets are SecretStr so they never show up in repr() or logs.
"""

import ipaddress
import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import Field, SecretStr, StringConstraints, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# The Meterless contract asks for a stable lowercase app id in the meterless-app header.
AppId = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
]

# Routing modes, not models: we pin exact Nemotron ids (decision 0001).
FORBIDDEN_MODEL_IDS = frozenset({"meterless/auto", "meterless/fusion"})
PLACEHOLDER_RUNTIME_TOKENS = frozenset({"rt-local"})
# Visible ASCII only: the token goes into an HTTP header verbatim.
RUNTIME_TOKEN_PATTERN = re.compile(r"[\x21-\x7e]{1,4096}")
# Nightshift runs on the host; from Docker it is host.docker.internal (still this machine).
LOCAL_HTTP_HOSTS = frozenset({"localhost", "host.docker.internal"})
# Pasted runtime URLs may point at an endpoint; these suffixes are cut back to the API root.
_METERLESS_ENDPOINT_SUFFIXES = ("/healthz", "/chat/completions", "/models", "/embeddings")


def is_local_host(host: str) -> bool:
    """Return True for loopback addresses and the names that reach the local runtime."""
    if host.lower() in LOCAL_HTTP_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_meterless_url(raw: str) -> str:
    """Return a pasted Meterless runtime URL as <scheme>://<host>[/prefix]/v1.

    Accepts the URL with or without a scheme (http is assumed), with or without /v1, or
    pointing at /v1/chat/completions, /v1/models, /v1/embeddings or /healthz. Query and
    fragment are dropped, and the result never ends in /v1/v1. Plain http is allowed only
    for the local runtime (loopback, localhost, host.docker.internal).

    Args:
        raw: The URL as configured.

    Returns:
        The normalised API base URL.

    Raises:
        ValueError: If the scheme is not http(s), the host is missing, the URL has credentials,
            the port is invalid, or plain http points at a remote host.
    """
    text = raw.strip()
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("must be an http(s) URL with a host")
    # Security: credentials in the URL would be sent along and could end up in logs.
    if "@" in parts.netloc:
        raise ValueError("must not contain credentials")
    try:
        parts.port  # noqa: B018  # parsing the port is the check
    except ValueError:
        raise ValueError("has an invalid port") from None
    # Security: the bearer token must not cross a network in clear text.
    if parts.scheme == "http" and not is_local_host(parts.hostname):
        raise ValueError("must use https for a non-local runtime")
    path = parts.path.rstrip("/")
    for suffix in _METERLESS_ENDPOINT_SUFFIXES:
        path = path.removesuffix(suffix)
    while path.endswith("/v1"):
        path = path.removesuffix("/v1")
    return f"{parts.scheme}://{parts.netloc}{path.rstrip('/')}/v1"


class SettingsError(RuntimeError):
    """Raised at startup when required settings are missing or invalid.

    The message names the variables only, never their values.
    """


class Settings(BaseSettings):
    """Typed application settings.

    Attributes:
        zetaris_rest_url: Base URL of the Zetaris REST API.
        zetaris_org_id: Zetaris organisation id (X-Org-ID).
        zetaris_admin_api_key: Zetaris admin key for setup scripts only; never used at runtime.
        zetaris_key_buyer: API key of the Zetaris user svc_buyer (role buyer).
        zetaris_key_risk: API key of the Zetaris user svc_risk (role risk_manager; director too).
        zetaris_user_agent: User agent sent to external sources.
        meterless_base_url: OpenAI-compatible Nightshift URL.
        meterless_runtime_token: Nightshift runtime token.
        meterless_app_id: Value of the meterless-app header.
        meterless_model: Lightning model id (research, evaluator).
        meterless_deep_model: Ultra model id (planner, recommender, writer).
        meterless_fast_model: Bolt model id (quick checks).
        meterless_embed_model: Embedding model id (H-MEM).
        context_service_url: URL of the World Model / H-MEM service.
        erp_database_url: ERP database URL (contains a password).
        app_database_url: Application database URL (contains a password).
        tool_cache_ttl_seconds: Time to live of the tool-result cache.
        log_level: Minimum level written to the logs.
        log_dir: Directory for the rotating JSON log files.
        app_env: dev enables /docs; prod (the default) does not.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    zetaris_rest_url: NonEmptyStr
    zetaris_org_id: NonEmptyStr
    # Security: admin, setup only, never used at runtime (the Zetaris client refuses it).
    zetaris_admin_api_key: SecretStr | None = None
    # Optional so the app starts before the role users exist (docs/zetaris-setup.md); the
    # Zetaris client refuses to start for a role whose key is missing.
    zetaris_key_buyer: SecretStr | None = None
    zetaris_key_risk: SecretStr | None = None
    zetaris_user_agent: NonEmptyStr
    meterless_base_url: NonEmptyStr
    meterless_runtime_token: SecretStr
    meterless_app_id: AppId
    meterless_model: NonEmptyStr
    meterless_deep_model: NonEmptyStr
    meterless_fast_model: NonEmptyStr
    meterless_embed_model: NonEmptyStr
    context_service_url: NonEmptyStr
    erp_database_url: SecretStr
    app_database_url: SecretStr
    tool_cache_ttl_seconds: Annotated[int, Field(ge=0, le=86400)] = 900
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_dir: Path = Path("logs")
    # Security: the safe default is prod, so API docs are never exposed by accident.
    app_env: Literal["dev", "prod"] = "prod"

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("meterless_runtime_token", "erp_database_url", "app_database_url")
    @classmethod
    def _secret_not_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be empty")
        return value

    @field_validator(
        "zetaris_admin_api_key", "zetaris_key_buyer", "zetaris_key_risk", mode="before"
    )
    @classmethod
    def _blank_key_is_unset(cls, value: object) -> object:
        # .env.example ships these empty; an empty key means "not created yet".
        if isinstance(value, str | SecretStr):
            raw = value.get_secret_value() if isinstance(value, SecretStr) else value
            if not raw.strip():
                return None
        return value

    @field_validator("meterless_base_url")
    @classmethod
    def _normalize_meterless_url(cls, value: str) -> str:
        return normalize_meterless_url(value)

    @field_validator("meterless_runtime_token")
    @classmethod
    def _real_runtime_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        # Security: CR/LF or spaces would break (or inject into) the Authorization header.
        if not RUNTIME_TOKEN_PATTERN.fullmatch(token):
            raise ValueError("must be visible ASCII without spaces")
        if token.lower() in PLACEHOLDER_RUNTIME_TOKENS:
            raise ValueError("is a placeholder; set the real runtime token")
        return value

    @field_validator(
        "meterless_model", "meterless_deep_model", "meterless_fast_model", "meterless_embed_model"
    )
    @classmethod
    def _exact_model_id(cls, value: str) -> str:
        if value.lower() in FORBIDDEN_MODEL_IDS:
            raise ValueError("must be an exact model id, not a routing mode")
        return value

    def secret_values(self) -> list[str]:
        """Return the raw value of every SecretStr setting, for the log redaction filter.

        URL secrets also contribute their password part, so a password logged on its own
        is redacted too.
        """
        values: list[str] = []
        for name in type(self).model_fields:
            field = getattr(self, name)
            if not isinstance(field, SecretStr):
                continue
            raw = field.get_secret_value().strip()
            if not raw:
                continue
            values.append(raw)
            try:
                password = urlsplit(raw).password
            except ValueError:
                password = None
            if password:
                # Drivers print the decoded password, the URL holds the encoded one: cover both.
                values.extend({password, unquote(password)})
        return values


def load_settings(env_file: str | None = ".env") -> Settings:
    """Load settings, turning validation errors into a SettingsError.

    Args:
        env_file: Path of the dotenv file, or None to read only the environment.

    Returns:
        The validated settings.

    Raises:
        SettingsError: If a required variable is missing or invalid. Names only, no values.
    """
    try:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err["loc"]})
        # Security: `from None` drops the chained error, which would echo input values.
        raise SettingsError(
            "Invalid or missing settings: " + ", ".join(names or ["unknown"])
        ) from None
