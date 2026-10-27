"""Tests for app.core.settings: loading, defaults, secrets and startup errors."""

import pytest
from pydantic import SecretStr

from app.core.settings import Settings, SettingsError, load_settings

SECRET_FIELDS = (
    "zetaris_app_api_key",
    "meterless_runtime_token",
    "erp_database_url",
    "app_database_url",
)


def test_loads_values_from_environment(env: dict[str, str]) -> None:
    settings = load_settings(env_file=None)
    assert settings.zetaris_rest_url == env["ZETARIS_REST_URL"]
    assert settings.meterless_deep_model == env["METERLESS_DEEP_MODEL"]
    assert settings.context_service_url == env["CONTEXT_SERVICE_URL"]


def test_defaults_are_safe(env: dict[str, str]) -> None:
    settings = load_settings(env_file=None)
    assert settings.tool_cache_ttl_seconds == 900
    assert settings.log_level == "INFO"
    assert str(settings.log_dir) == "logs"
    assert settings.app_env == "prod"


@pytest.mark.parametrize("field", SECRET_FIELDS)
def test_secret_fields_are_secretstr_and_not_in_repr(settings: Settings, field: str) -> None:
    value = getattr(settings, field)
    assert isinstance(value, SecretStr)
    assert value.get_secret_value() not in repr(settings)
    assert value.get_secret_value() not in str(settings)


def test_secret_values_include_every_secret_and_database_passwords(settings: Settings) -> None:
    values = settings.secret_values()
    for field in SECRET_FIELDS:
        assert getattr(settings, field).get_secret_value() in values
    assert "erp-pass-VALUE-3333" in values
    assert "app-pass-VALUE-4444" in values


def test_missing_required_value_names_the_variable(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ZETARIS_APP_API_KEY")
    with pytest.raises(SettingsError) as info:
        load_settings(env_file=None)
    message = str(info.value)
    assert "ZETARIS_APP_API_KEY" in message
    for value in env.values():
        assert value not in message


def test_reports_all_missing_variables_at_once(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("METERLESS_MODEL")
    monkeypatch.delenv("CONTEXT_SERVICE_URL")
    with pytest.raises(SettingsError) as info:
        load_settings(env_file=None)
    assert "METERLESS_MODEL" in str(info.value)
    assert "CONTEXT_SERVICE_URL" in str(info.value)


def test_empty_value_counts_as_missing(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METERLESS_APP_ID", "")
    with pytest.raises(SettingsError, match="METERLESS_APP_ID"):
        load_settings(env_file=None)


@pytest.mark.parametrize(
    ("name", "bad_value"),
    [("APP_ENV", "staging"), ("TOOL_CACHE_TTL_SECONDS", "abc"), ("LOG_LEVEL", "LOUD")],
)
def test_invalid_value_names_variable_but_not_value(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, name: str, bad_value: str
) -> None:
    monkeypatch.setenv(name, bad_value)
    with pytest.raises(SettingsError) as info:
        load_settings(env_file=None)
    assert name in str(info.value)
    assert bad_value not in str(info.value)


def test_settings_error_hides_the_original_validation_error(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(SettingsError) as info:
        load_settings(env_file=None)
    # The pydantic error would echo input values; it must not be chained.
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_log_level_is_case_insensitive(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert load_settings(env_file=None).log_level == "DEBUG"
