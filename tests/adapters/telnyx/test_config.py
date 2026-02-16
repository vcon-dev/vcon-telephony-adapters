"""Tests for Telnyx configuration."""

import pytest
from adapters.telnyx.config import TelnyxConfig


class TestTelnyxConfig:
    """Tests for TelnyxConfig class."""

    def test_minimal_config(self, monkeypatch):
        """Test config with only required CONSERVER_URL."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        config = TelnyxConfig()
        assert config.conserver_url == "https://conserver.example.com/vcon"
        assert config.telnyx_api_url == "https://api.telnyx.com/v2"

    def test_missing_conserver_url(self, monkeypatch):
        """Test that missing CONSERVER_URL raises error."""
        monkeypatch.delenv("CONSERVER_URL", raising=False)
        with pytest.raises(ValueError, match="CONSERVER_URL"):
            TelnyxConfig()

    def test_telnyx_settings(self, monkeypatch):
        """Test Telnyx-specific settings."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("TELNYX_API_KEY", "KEY123")
        monkeypatch.setenv("TELNYX_API_SECRET", "SECRET456")
        monkeypatch.setenv("TELNYX_API_URL", "https://custom-api.telnyx.com/v2")

        config = TelnyxConfig()

        assert config.telnyx_api_key == "KEY123"
        assert config.telnyx_api_secret == "SECRET456"
        assert config.telnyx_api_url == "https://custom-api.telnyx.com/v2"

    def test_webhook_validation_enabled_by_default(self, monkeypatch):
        """Test webhook validation is enabled by default."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")

        config = TelnyxConfig()

        assert config.validate_webhook is True

    def test_webhook_validation_disabled(self, monkeypatch):
        """Test webhook validation can be disabled."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_TELNYX_WEBHOOK", "false")

        config = TelnyxConfig()

        assert config.validate_webhook is False

    def test_telnyx_public_key(self, monkeypatch):
        """Test Telnyx public key for webhook validation."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("TELNYX_PUBLIC_KEY", "PUBLIC_KEY_BASE64")

        config = TelnyxConfig()

        assert config.telnyx_public_key == "PUBLIC_KEY_BASE64"

    def test_get_api_headers(self, monkeypatch):
        """Test get_api_headers method."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("TELNYX_API_KEY", "KEY123")

        config = TelnyxConfig()
        headers = config.get_api_headers()

        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"
        assert headers["Authorization"] == "Bearer KEY123"

    def test_get_api_headers_no_key(self, monkeypatch):
        """Test get_api_headers without API key."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")

        config = TelnyxConfig()
        headers = config.get_api_headers()

        assert "Authorization" not in headers

    def test_state_file_default(self, monkeypatch):
        """Test Telnyx-specific state file default."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")

        config = TelnyxConfig()

        assert config.state_file == ".telnyx_adapter_state.json"

    def test_webhook_url(self, monkeypatch):
        """Test webhook URL setting."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("TELNYX_WEBHOOK_URL", "https://webhook.example.com/telnyx")

        config = TelnyxConfig()

        assert config.webhook_url == "https://webhook.example.com/telnyx"
