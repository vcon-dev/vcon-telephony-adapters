"""Tests for ElevenLabs configuration."""

import pytest

from adapters.elevenlabs.config import ElevenLabsConfig


class TestElevenLabsConfig:
    def test_minimal_config(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_ELEVENLABS_WEBHOOK", "false")
        monkeypatch.delenv("ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS", raising=False)
        monkeypatch.delenv("WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", raising=False)
        config = ElevenLabsConfig()
        assert config.conserver_url == "https://conserver.example.com/vcon"
        # Falls through to ElevenLabs' own provider default (1800s, matching
        # its SDK verifier's default) when neither the per-platform override
        # nor the shared WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS is set.
        assert config.webhook_timestamp_tolerance_seconds == 1800

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
        assert config.webhook_timestamp_tolerance_seconds == 60

    def test_shared_tolerance_applies_without_override(self, monkeypatch):
        """WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS (shared) applies when the
        per-platform ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS override is unset."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_ELEVENLABS_WEBHOOK", "false")
        monkeypatch.delenv("ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS", raising=False)
        monkeypatch.setenv("WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", "45")
        config = ElevenLabsConfig()
        assert config.webhook_timestamp_tolerance_seconds == 45

    def test_override_wins_over_shared_tolerance(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_ELEVENLABS_WEBHOOK", "false")
        monkeypatch.setenv("WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", "45")
        monkeypatch.setenv("ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS", "60")
        config = ElevenLabsConfig()
        assert config.webhook_timestamp_tolerance_seconds == 60

    def test_invalid_tolerance_refuses_to_start(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_ELEVENLABS_WEBHOOK", "false")
        monkeypatch.setenv("ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS", "not-a-number")
        with pytest.raises(ValueError, match="ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS"):
            ElevenLabsConfig()

    def test_negative_tolerance_refuses_to_start(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_ELEVENLABS_WEBHOOK", "false")
        monkeypatch.setenv("ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS", "-5")
        with pytest.raises(ValueError, match="ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS"):
            ElevenLabsConfig()
