import pytest

from app.config import ConfigError, load_credentials, load_settings


def test_env_is_explicit_no_ambient_values_or_interpolation(tmp_path, monkeypatch):
    monkeypatch.setenv("UPBIT_SECRET_KEY", "ambient-secret")
    monkeypatch.setenv("SENTINEL", "ambient-substitution")
    path = tmp_path / ".env"
    path.write_text(
        "OKX_API_KEY='${SENTINEL}'\nRPC_URL_99999=https://rpc.invalid/test\n"
        "WALLET_PRIVATE_KEY=\nDRY_RUN=true\n"
    )
    credentials = load_credentials(path)
    assert credentials.okx_api_key.get_secret_value() == "${SENTINEL}"
    assert credentials.upbit_secret_key.get_secret_value() == ""
    assert credentials.wallet_private_key.get_secret_value() == ""
    assert set(credentials.rpc_urls) == {"99999"}
    assert credentials.dry_run is True
    assert "rpc.invalid" not in repr(credentials)


@pytest.mark.parametrize(
    "content",
    [
        "DRY_RUN=yes\n",
        "OKX_API_KEY=one\nOKX_API_KEY=two\n",
        "RPC_URL_foo=secret\n",
        "RPC_URL_01=secret\n",
        "OKX_SECRET_KEY='unterminated\n",
    ],
)
def test_bad_env_has_safe_error(tmp_path, capsys, content):
    path = tmp_path / ".env"
    path.write_text(content)
    with pytest.raises(ConfigError) as caught:
        load_credentials(path)
    assert content.strip() not in str(caught.value)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("raw", ["yes", "no", "1", "0", "", "True", "FALSE", "on", "off"])
def test_dry_run_accepts_only_literal_true_or_false(tmp_path, raw):
    path = tmp_path / ".env"
    path.write_text(f"DRY_RUN={raw}\n")
    with pytest.raises(ConfigError, match="DRY_RUN"):
        load_credentials(path)


def test_dry_run_missing_defaults_to_true_and_false_is_explicit(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OKX_API_KEY=x\n")
    assert load_credentials(path).dry_run is True
    path.write_text("DRY_RUN=false\n")
    assert load_credentials(path).dry_run is False


def test_settings_default_and_loopback_enforcement(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text("")
    settings = load_settings(path)
    assert settings.server.host == "127.0.0.1"
    assert settings.coingecko.plan == "demo"
    assert settings.registry.max_age_hours == 72
    path.write_text("[registry]\nmax_age_hours = 12.5\n")
    assert load_settings(path).registry.max_age_hours == 12.5
    path.write_text('[server]\nhost="0.0.0.0"\n')
    with pytest.raises(ConfigError):
        load_settings(path)
    path.write_text("[registry]\nmax_age_hours = 0\n")
    with pytest.raises(ConfigError):
        load_settings(path)


def test_missing_env_is_clear(tmp_path):
    with pytest.raises(ConfigError, match=".env"):
        load_credentials(tmp_path / "absent")
