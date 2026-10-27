"""Tests for app.core.settings: loading, defaults, secrets and startup errors."""

from collections.abc import Callable

import pytest
from pydantic import SecretStr, ValidationError

from app.core.settings import Settings, SettingsError, load_settings

SECRET_FIELDS = (
    "zetaris_admin_api_key",
    "zetaris_key_buyer",
    "zetaris_key_risk",
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
    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN")
    with pytest.raises(SettingsError) as info:
        load_settings(env_file=None)
    message = str(info.value)
    assert "METERLESS_RUNTIME_TOKEN" in message
    for value in env.values():
        assert value not in message


@pytest.mark.parametrize("name", ["ZETARIS_ADMIN_API_KEY", "ZETARIS_KEY_BUYER", "ZETARIS_KEY_RISK"])
@pytest.mark.parametrize("unset", ["delete", "empty", "blank"])
def test_zetaris_keys_are_optional_and_empty_means_unset(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, name: str, unset: str
) -> None:
    """The role keys are pasted later (docs/zetaris-setup.md); a missing one is not a crash."""
    if unset == "delete":
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, "" if unset == "empty" else "   ")
    settings = load_settings(env_file=None)
    assert getattr(settings, name.lower()) is None


def test_the_retired_app_key_variable_is_not_read(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ZETARIS_APP_API_KEY held an admin key; no setting may pick it up any more."""
    monkeypatch.setenv("ZETARIS_APP_API_KEY", "zk-old-admin-VALUE-7777")
    settings = load_settings(env_file=None)
    assert "zk-old-admin-VALUE-7777" not in [
        getattr(settings, f).get_secret_value() for f in SECRET_FIELDS
    ]
    assert not hasattr(settings, "zetaris_app_api_key")


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


@pytest.mark.parametrize(
    "raw",
    [
        "http://127.0.0.1:8088/v1",
        "http://127.0.0.1:8088",
        "http://127.0.0.1:8088/",
        "127.0.0.1:8088",
        "127.0.0.1:8088/v1/",
        "http://127.0.0.1:8088/v1/chat/completions",
        "http://127.0.0.1:8088/v1/models",
        "http://127.0.0.1:8088/v1/embeddings",
        "http://127.0.0.1:8088/healthz",
        "http://127.0.0.1:8088/v1/v1",
        "  http://127.0.0.1:8088/v1?x=1#frag  ",
    ],
)
def test_meterless_base_url_is_normalised(make_settings: Callable[..., Settings], raw: str) -> None:
    assert make_settings(meterless_base_url=raw).meterless_base_url == "http://127.0.0.1:8088/v1"


def test_meterless_base_url_keeps_https_and_path_prefix(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(meterless_base_url="https://gw.example.com/nightshift/v1/models")
    assert settings.meterless_base_url == "https://gw.example.com/nightshift/v1"


@pytest.mark.parametrize(
    "raw", ["ftp://127.0.0.1:8088/v1", "http:///v1", "http://user:pw@127.0.0.1:8088/v1"]
)
def test_meterless_base_url_rejects_bad_urls(
    make_settings: Callable[..., Settings], raw: str
) -> None:
    with pytest.raises(ValidationError):
        make_settings(meterless_base_url=raw)


@pytest.mark.parametrize(
    "field",
    ["meterless_model", "meterless_deep_model", "meterless_fast_model", "meterless_embed_model"],
)
@pytest.mark.parametrize("model_id", ["meterless/auto", "meterless/fusion", " Meterless/Auto "])
def test_routing_modes_are_rejected_as_model_ids(
    make_settings: Callable[..., Settings], field: str, model_id: str
) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: model_id})


def test_routing_mode_error_names_the_variable(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METERLESS_DEEP_MODEL", "meterless/fusion")
    with pytest.raises(SettingsError, match="METERLESS_DEEP_MODEL"):
        load_settings(env_file=None)


@pytest.mark.parametrize("token", ["rt-local", " rt-local "])
def test_placeholder_runtime_token_is_rejected(
    make_settings: Callable[..., Settings], token: str
) -> None:
    with pytest.raises(ValidationError):
        make_settings(meterless_runtime_token=token)


@pytest.mark.parametrize("app_id", ["Supplier-Risk", "supplier risk", "-x", "a" * 65])
def test_app_id_must_be_a_stable_lowercase_id(
    make_settings: Callable[..., Settings], app_id: str
) -> None:
    with pytest.raises(ValidationError):
        make_settings(meterless_app_id=app_id)


@pytest.mark.parametrize(
    "raw",
    [
        "http://localhost:8088/v1",
        "http://127.0.0.2:8088/v1",
        "http://[::1]:8088/v1",
        "http://host.docker.internal:8088/v1",
        "https://nightshift.example.com/v1",
    ],
)
def test_plain_http_is_allowed_only_for_local_hosts(
    make_settings: Callable[..., Settings], raw: str
) -> None:
    assert make_settings(meterless_base_url=raw).meterless_base_url == raw


@pytest.mark.parametrize("raw", ["http://10.0.0.5:8088/v1", "nightshift.example.com"])
def test_plain_http_to_a_remote_host_is_rejected(
    make_settings: Callable[..., Settings], raw: str
) -> None:
    with pytest.raises(ValidationError):
        make_settings(meterless_base_url=raw)


@pytest.mark.parametrize("token", ["has space", "tab\tinside", "RT-Local", "x" * 4097])
def test_runtime_token_must_be_visible_ascii(
    make_settings: Callable[..., Settings], token: str
) -> None:
    with pytest.raises(ValidationError):
        make_settings(meterless_runtime_token=token)
