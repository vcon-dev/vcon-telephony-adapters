"""Tests for Bandwidth configuration."""

import pytest

from adapters.bandwidth.config import BandwidthConfig


class TestBandwidthConfig:
    """Tests for BandwidthConfig class."""

    def test_minimal_config(self, monkeypatch):
        """Test config with only required CONSERVER_URL."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_BANDWIDTH_WEBHOOK", "false")
        config = BandwidthConfig()
        assert config.conserver_url == "https://conserver.example.com/vcon"
        assert config.bandwidth_voice_api_url == "https://voice.bandwidth.com/api/v2"

    def test_missing_conserver_url(self, monkeypatch):
        """Test that missing CONSERVER_URL raises error."""
        monkeypatch.delenv("CONSERVER_URL", raising=False)
        with pytest.raises(ValueError, match="CONSERVER_URL"):
            BandwidthConfig()

    def test_bandwidth_settings(self, monkeypatch):
        """Test Bandwidth-specific settings."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_BANDWIDTH_WEBHOOK", "false")
        monkeypatch.setenv("BANDWIDTH_ACCOUNT_ID", "123456")
        monkeypatch.setenv("BANDWIDTH_USERNAME", "api-user")
        monkeypatch.setenv("BANDWIDTH_PASSWORD", "api-password")
        monkeypatch.setenv("BANDWIDTH_APPLICATION_ID", "app-789")

        config = BandwidthConfig()

        assert config.bandwidth_account_id == "123456"
        assert config.bandwidth_username == "api-user"
        assert config.bandwidth_password == "api-password"
        assert config.bandwidth_application_id == "app-789"

    def test_custom_api_url(self, monkeypatch):
        """Test custom Bandwidth API URL."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_BANDWIDTH_WEBHOOK", "false")
        monkeypatch.setenv("BANDWIDTH_VOICE_API_URL", "https://custom-api.bandwidth.com")

        config = BandwidthConfig()

        assert config.bandwidth_voice_api_url == "https://custom-api.bandwidth.com"

    def test_webhook_validation_enabled_by_default(self, monkeypatch):
        """Test webhook validation is enabled by default."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("BANDWIDTH_WEBHOOK_USERNAME", "webhook-user")
        monkeypatch.setenv("BANDWIDTH_WEBHOOK_PASSWORD", "webhook-pass")

        config = BandwidthConfig()

        assert config.validate_webhook is True

    def test_webhook_validation_disabled(self, monkeypatch):
        """Test webhook validation can be disabled."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_BANDWIDTH_WEBHOOK", "false")

        config = BandwidthConfig()

        assert config.validate_webhook is False

    def test_webhook_credentials(self, monkeypatch):
        """Test webhook authentication credentials."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("BANDWIDTH_WEBHOOK_USERNAME", "webhook-user")
        monkeypatch.setenv("BANDWIDTH_WEBHOOK_PASSWORD", "webhook-pass")

        config = BandwidthConfig()

        assert config.webhook_username == "webhook-user"
        assert config.webhook_password == "webhook-pass"

    def test_get_api_auth(self, monkeypatch):
        """Test get_api_auth method."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_BANDWIDTH_WEBHOOK", "false")
        monkeypatch.setenv("BANDWIDTH_USERNAME", "api-user")
        monkeypatch.setenv("BANDWIDTH_PASSWORD", "api-pass")

        config = BandwidthConfig()
        auth = config.get_api_auth()

        assert auth == ("api-user", "api-pass")

    def test_get_api_headers(self, monkeypatch):
        """Test get_api_headers method."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_BANDWIDTH_WEBHOOK", "false")

        config = BandwidthConfig()
        headers = config.get_api_headers()

        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"

    def test_state_file_default(self, monkeypatch):
        """Test Bandwidth-specific state file default."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_BANDWIDTH_WEBHOOK", "false")

        config = BandwidthConfig()

        assert config.state_file == ".bandwidth_adapter_state.json"


class TestBandwidthConfigFailClosed:
    """Webhook validation refuses to start unless both credentials are configured."""

    def test_validation_enabled_without_credentials_refuses_to_start(self, monkeypatch):
        """Default (validation on) with no credentials raises rather than fail open."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.delenv("BANDWIDTH_WEBHOOK_USERNAME", raising=False)
        monkeypatch.delenv("BANDWIDTH_WEBHOOK_PASSWORD", raising=False)

        with pytest.raises(ValueError, match="BANDWIDTH_WEBHOOK_USERNAME"):
            BandwidthConfig()

    def test_validation_enabled_with_partial_credentials_refuses_to_start(self, monkeypatch):
        """Only one of username/password set is still an unauthenticated webhook."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("BANDWIDTH_WEBHOOK_USERNAME", "webhook-user")
        monkeypatch.delenv("BANDWIDTH_WEBHOOK_PASSWORD", raising=False)

        with pytest.raises(ValueError, match="BANDWIDTH_WEBHOOK_USERNAME"):
            BandwidthConfig()

    def test_allow_unsigned_webhooks_bypasses_the_refusal(self, monkeypatch):
        """ALLOW_UNSIGNED_WEBHOOKS=true is the documented opt-out."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
        monkeypatch.delenv("BANDWIDTH_WEBHOOK_USERNAME", raising=False)
        monkeypatch.delenv("BANDWIDTH_WEBHOOK_PASSWORD", raising=False)

        config = BandwidthConfig()

        assert config.validate_webhook is True
        assert config.allow_unsigned_webhooks is True
