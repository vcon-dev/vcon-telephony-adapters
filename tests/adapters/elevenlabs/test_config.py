"""Tests for ElevenLabs configuration."""

import pytest

from adapters.elevenlabs.config import ElevenLabsConfig


class TestElevenLabsConfig:
    def test_minimal_config(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_ELEVENLABS_WEBHOOK", "false")
        config = ElevenLabsConfig()
        assert config.conserver_url == "https://conserver.example.com/vcon"
        assert config.webhook_tolerance_seconds == 1800

    def test_missing_conserver_url(self, monkeypatch):
        monkeypatch.delenv("CONSERVER_URL", raising=False)
        with pytest.raises(ValueError, match="CONSERVER_URL"):
            ElevenLabsConfig()

    def test_webhook_validation_enabled_by_default(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", "shh")
        config = ElevenLabsConfig()
        assert config.validate_webhook is True

    def test_missing_secret_refuses_to_start(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.delenv("ELEVENLABS_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("ALLOW_UNSIGNED_WEBHOOKS", raising=False)
        with pytest.raises(ValueError, match="ELEVENLABS_WEBHOOK_SECRET"):
            ElevenLabsConfig()

    def test_missing_secret_allowed_with_opt_out(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.delenv("ELEVENLABS_WEBHOOK_SECRET", raising=False)
        monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
        config = ElevenLabsConfig()
        assert config.webhook_secret is None

    def test_custom_tolerance(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_ELEVENLABS_WEBHOOK", "false")
        monkeypatch.setenv("ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS", "60")
        config = ElevenLabsConfig()
        assert config.webhook_tolerance_seconds == 60
