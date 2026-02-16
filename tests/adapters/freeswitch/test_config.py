"""Tests for FreeSWITCH configuration."""

import os
import pytest
from adapters.freeswitch.config import FreeSwitchConfig


class TestFreeSwitchConfig:
    """Tests for FreeSwitchConfig class."""

    def test_minimal_config(self, monkeypatch):
        """Test config with only required CONSERVER_URL."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        config = FreeSwitchConfig()
        assert config.conserver_url == "https://conserver.example.com/vcon"
        assert config.host == "0.0.0.0"
        assert config.port == 8080
        assert config.freeswitch_host == "localhost"
        assert config.freeswitch_esl_port == 8021

    def test_missing_conserver_url(self, monkeypatch):
        """Test that missing CONSERVER_URL raises error."""
        monkeypatch.delenv("CONSERVER_URL", raising=False)
        with pytest.raises(ValueError, match="CONSERVER_URL"):
            FreeSwitchConfig()

    def test_freeswitch_settings(self, monkeypatch):
        """Test FreeSWITCH-specific settings."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("FREESWITCH_HOST", "fs.example.com")
        monkeypatch.setenv("FREESWITCH_ESL_PORT", "8022")
        monkeypatch.setenv("FREESWITCH_ESL_PASSWORD", "secret123")
        monkeypatch.setenv("FREESWITCH_RECORDINGS_PATH", "/data/recordings")
        monkeypatch.setenv("FREESWITCH_RECORDINGS_URL_BASE", "https://fs.example.com/recordings")

        config = FreeSwitchConfig()

        assert config.freeswitch_host == "fs.example.com"
        assert config.freeswitch_esl_port == 8022
        assert config.freeswitch_esl_password == "secret123"
        assert config.recordings_path == "/data/recordings"
        assert config.recordings_url_base == "https://fs.example.com/recordings"

    def test_webhook_validation_settings(self, monkeypatch):
        """Test webhook validation settings."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("FREESWITCH_WEBHOOK_SECRET", "webhook-secret")
        monkeypatch.setenv("VALIDATE_FREESWITCH_WEBHOOK", "true")

        config = FreeSwitchConfig()

        assert config.webhook_secret == "webhook-secret"
        assert config.validate_webhook is True

    def test_webhook_validation_disabled(self, monkeypatch):
        """Test webhook validation defaults to disabled."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")

        config = FreeSwitchConfig()

        assert config.validate_webhook is False

    def test_state_file_default(self, monkeypatch):
        """Test FreeSWITCH-specific state file default."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")

        config = FreeSwitchConfig()

        assert config.state_file == ".freeswitch_adapter_state.json"

    def test_state_file_override(self, monkeypatch):
        """Test state file can be overridden."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", "/var/lib/custom_state.json")

        config = FreeSwitchConfig()

        assert config.state_file == "/var/lib/custom_state.json"
