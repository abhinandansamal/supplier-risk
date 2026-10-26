"""Application settings loaded from the environment (and .env) with pydantic-settings.

All configuration lives here. Secrets are SecretStr so they never show up in repr() or logs.
"""

from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import Field, SecretStr, StringConstraints, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SettingsError(RuntimeError):
    """Raised at startup when required settings are missing or invalid.

    The message names the variables only, never their values.
    """


class Settings(BaseSettings):
    """Typed application settings.

    Attributes:
        zetaris_rest_url: Base URL of the Zetaris REST API.
        zetaris_org_id: Zetaris organisation id (X-Org-ID).
        zetaris_app_api_key: Read-only app user key for Zetaris.
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
    zetaris_app_api_key: SecretStr
    zetaris_user_agent: NonEmptyStr
    meterless_base_url: NonEmptyStr
    meterless_runtime_token: SecretStr
    meterless_app_id: NonEmptyStr
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

    @field_validator(
        "zetaris_app_api_key", "meterless_runtime_token", "erp_database_url", "app_database_url"
    )
    @classmethod
    def _secret_not_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be empty")
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
