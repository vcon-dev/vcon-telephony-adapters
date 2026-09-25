"""Tests for FreeSWITCH configuration."""

import pytest

from adapters.freeswitch.config import FreeSwitchConfig


class TestFreeSwitchConfig:
    """Tests for FreeSwitchConfig class."""

    def test_minimal_config(self, monkeypatch):
        """Test config with only required CONSERVER_URL."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_FREESWITCH_WEBHOOK", "false")
        config = FreeSwitchConfig()
        assert config.conserver_url == "https://conserver.example.com/vcon"
        assert config.host == "0.0.0.0"
        assert config.port == 8080
        assert config.freeswitch_host == "localhost"
        assert config.freeswitch_esl_port == 8021

    def test_esl_password_has_no_stock_default(self, monkeypatch):
        """FREESWITCH_ESL_PASSWORD is not defaulted to the FreeSWITCH stock password."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_FREESWITCH_WEBHOOK", "false")
        monkeypatch.delenv("FREESWITCH_ESL_PASSWORD", raising=False)

        config = FreeSwitchConfig()

        assert config.freeswitch_esl_password is None

    def test_missing_conserver_url(self, monkeypatch):
        """Test that missing CONSERVER_URL raises error."""
        monkeypatch.delenv("CONSERVER_URL", raising=False)
        with pytest.raises(ValueError, match="CONSERVER_URL"):
            FreeSwitchConfig()

    def test_freeswitch_settings(self, monkeypatch):
        """Test FreeSWITCH-specific settings."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_FREESWITCH_WEBHOOK", "false")
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

    def test_webhook_validation_enabled_by_default(self, monkeypatch):
        """Test webhook validation defaults to enabled."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("FREESWITCH_WEBHOOK_SECRET", "webhook-secret")

        config = FreeSwitchConfig()

        assert config.validate_webhook is True

    def test_webhook_validation_can_be_disabled(self, monkeypatch):
        """Test webhook validation can be explicitly disabled."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_FREESWITCH_WEBHOOK", "false")

        config = FreeSwitchConfig()

        assert config.validate_webhook is False

    def test_state_file_default(self, monkeypatch):
        """Test FreeSWITCH-specific state file default."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_FREESWITCH_WEBHOOK", "false")

        config = FreeSwitchConfig()

        assert config.state_file == ".freeswitch_adapter_state.json"

    def test_state_file_override(self, monkeypatch):
        """Test state file can be overridden."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_FREESWITCH_WEBHOOK", "false")
        monkeypatch.setenv("STATE_FILE", "/var/lib/custom_state.json")

        config = FreeSwitchConfig()

        assert config.state_file == "/var/lib/custom_state.json"


class TestFreeSwitchConfigFailClosed:
    """Webhook validation refuses to start unless a secret is configured."""

    def test_validation_enabled_without_secret_refuses_to_start(self, monkeypatch):
        """Default (validation on) with no secret raises rather than fail open."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.delenv("FREESWITCH_WEBHOOK_SECRET", raising=False)

        with pytest.raises(ValueError, match="FREESWITCH_WEBHOOK_SECRET"):
            FreeSwitchConfig()

    def test_allow_unsigned_webhooks_bypasses_the_refusal(self, monkeypatch):
        """ALLOW_UNSIGNED_WEBHOOKS=true is the documented opt-out."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
        monkeypatch.delenv("FREESWITCH_WEBHOOK_SECRET", raising=False)

        config = FreeSwitchConfig()

        assert config.validate_webhook is True
        assert config.webhook_secret is None
        assert config.allow_unsigned_webhooks is True
