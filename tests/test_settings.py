from __future__ import annotations

import json

import pytest

import tsdp2.config as config


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    credentials = {}
    monkeypatch.setattr(config, "_SETTINGS_FILE", settings_path)
    monkeypatch.setattr(config, "_credential_get", lambda: credentials.get("token", ""))
    monkeypatch.setattr(
        config,
        "_credential_set",
        lambda value: credentials.__setitem__("token", value) or True,
    )
    monkeypatch.delenv(config._TELEGRAM_TOKEN_ENV, raising=False)
    return settings_path, credentials


def test_plaintext_token_migrates_out_of_json(isolated_settings):
    path, credentials = isolated_settings
    path.write_text(
        json.dumps({"tg_token": "123:legacy-secret", "tg_chat_id": "42"}),
        encoding="utf-8",
    )
    loaded = config.load_settings()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["tg_token"] == "123:legacy-secret"
    assert credentials["token"] == "123:legacy-secret"
    assert "tg_token" not in on_disk
    assert on_disk["tg_chat_id"] == "42"


def test_environment_token_has_priority(isolated_settings, monkeypatch):
    path, credentials = isolated_settings
    credentials["token"] = "credential-token"
    monkeypatch.setenv(config._TELEGRAM_TOKEN_ENV, "environment-token")
    assert config.load_settings()["tg_token"] == "environment-token"
    assert not path.exists()


def test_save_settings_is_atomic_and_excludes_token(isolated_settings):
    path, credentials = isolated_settings
    config.save_settings({"tg_token": "secure-token", "tg_enabled": True, "workers": 3})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert credentials["token"] == "secure-token"
    assert payload == {"tg_enabled": True, "workers": 3}
    assert not list(path.parent.glob("*.tmp"))


def test_invalid_settings_json_returns_defaults(isolated_settings):
    path, _credentials = isolated_settings
    path.write_text("{broken", encoding="utf-8")
    assert config.load_settings() == {}
