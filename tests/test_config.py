"""Tests for environment-driven configuration."""

import importlib

import pytest

from app import config


def _reload(monkeypatch, **env):
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    importlib.reload(config)


def test_storage_paths_default_to_the_container_layout(monkeypatch):
    cfg = _reload(monkeypatch, DATA_DIR=None, AUDIO_DIR=None)
    assert str(cfg.DATA_DIR) == "/app/data"
    assert str(cfg.AUDIO_DIR) == "/app/audio"
    assert cfg.DB_PATH == "/app/data/audiobook.db"


def test_storage_paths_are_overridable_for_local_development(monkeypatch, tmp_path):
    """The paths used to be hardcoded, so running outside Docker was impossible."""
    cfg = _reload(monkeypatch, DATA_DIR=str(tmp_path / "d"), AUDIO_DIR=str(tmp_path / "a"))
    assert cfg.DB_PATH == str(tmp_path / "d" / "audiobook.db")
    assert cfg.AUDIO_DIR == tmp_path / "a"


def test_relative_paths_are_allowed(monkeypatch):
    cfg = _reload(monkeypatch, DATA_DIR="./data", AUDIO_DIR="./audio")
    assert cfg.DB_PATH == "data/audiobook.db"


def test_integer_settings_reject_nonsense(monkeypatch):
    cfg = _reload(monkeypatch, MAX_CONCURRENT_JOBS="not-a-number")
    assert cfg.MAX_CONCURRENT_JOBS == 2


def test_integer_settings_are_clamped_to_a_sane_minimum(monkeypatch):
    cfg = _reload(monkeypatch, MAX_CONCURRENT_JOBS="0")
    assert cfg.MAX_CONCURRENT_JOBS == 1


def test_the_kokoro_idle_unload_can_be_switched_off(monkeypatch):
    """0 is a real setting here — keep the model (and its VRAM) resident — so it
    must not be clamped up to 1 the way the other integer settings are."""
    assert _reload(monkeypatch, KOKORO_IDLE_UNLOAD_SECONDS="0").KOKORO_IDLE_UNLOAD_SECONDS == 0
    assert _reload(monkeypatch, KOKORO_IDLE_UNLOAD_SECONDS=None).KOKORO_IDLE_UNLOAD_SECONDS == 300


def test_auth_is_off_unless_both_halves_are_set(monkeypatch):
    assert not _reload(monkeypatch, AUTH_USERNAME="alice", AUTH_PASSWORD=None).auth_enabled()
    assert not _reload(monkeypatch, AUTH_USERNAME=None, AUTH_PASSWORD="pw").auth_enabled()
    assert _reload(monkeypatch, AUTH_USERNAME="alice", AUTH_PASSWORD="pw").auth_enabled()


def test_trusted_origins_are_split_and_normalised(monkeypatch):
    cfg = _reload(monkeypatch, TRUSTED_ORIGINS="https://a.example/, https://b.example ,")
    assert cfg.TRUSTED_ORIGINS == ("https://a.example", "https://b.example")


def test_no_trusted_origins_by_default(monkeypatch):
    assert _reload(monkeypatch, TRUSTED_ORIGINS=None).TRUSTED_ORIGINS == ()


def test_base_url_trailing_slash_is_stripped(monkeypatch):
    cfg = _reload(monkeypatch, READECK_BASE_URL="https://readeck.example.com/")
    assert cfg.READECK_BASE_URL == "https://readeck.example.com"
