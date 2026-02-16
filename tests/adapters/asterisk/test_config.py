"""Tests for Asterisk configuration."""

import pytest
from adapters.asterisk.config import AsteriskConfig


class TestAsteriskConfig:
    """Tests for AsteriskConfig class."""

    def test_minimal_config(self, monkeypatch):
        """Test config with only required CONSERVER_URL."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        config = AsteriskConfig()
        assert config.conserver_url == "https://conserver.example.com/vcon"
        assert config.asterisk_host == "localhost"
        assert config.asterisk_ari_port == 8088
        assert config.asterisk_ari_username == "asterisk"

    def test_missing_conserver_url(self, monkeypatch):
        """Test that missing CONSERVER_URL raises error."""
        monkeypatch.delenv("CONSERVER_URL", raising=False)
        with pytest.raises(ValueError, match="CONSERVER_URL"):
            AsteriskConfig()

    def test_asterisk_settings(self, monkeypatch):
        """Test Asterisk-specific settings."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("ASTERISK_HOST", "ast.example.com")
        monkeypatch.setenv("ASTERISK_ARI_PORT", "8089")
        monkeypatch.setenv("ASTERISK_ARI_USERNAME", "admin")
        monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "secret123")
        monkeypatch.setenv("ASTERISK_RECORDINGS_PATH", "/data/recordings")

        config = AsteriskConfig()

        assert config.asterisk_host == "ast.example.com"
        assert config.asterisk_ari_port == 8089
        assert config.asterisk_ari_username == "admin"
        assert config.asterisk_ari_password == "secret123"
        assert config.recordings_path == "/data/recordings"

    def test_ari_url_default(self, monkeypatch):
        """Test ARI URL default construction."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("ASTERISK_HOST", "ast.example.com")
        monkeypatch.setenv("ASTERISK_ARI_PORT", "8088")

        config = AsteriskConfig()

        assert config.asterisk_ari_url == "http://ast.example.com:8088"

    def test_ari_url_https(self, monkeypatch):
        """Test ARI URL with HTTPS."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("ASTERISK_ARI_SCHEME", "https")
        monkeypatch.setenv("ASTERISK_HOST", "ast.example.com")

        config = AsteriskConfig()

        assert config.asterisk_ari_url.startswith("https://")

    def test_ari_url_override(self, monkeypatch):
        """Test ARI URL can be overridden."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("ASTERISK_ARI_URL", "https://custom-ari.example.com:9000")

        config = AsteriskConfig()

        assert config.asterisk_ari_url == "https://custom-ari.example.com:9000"

    def test_get_ari_auth(self, monkeypatch):
        """Test get_ari_auth method."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("ASTERISK_ARI_USERNAME", "admin")
        monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "secret")

        config = AsteriskConfig()
        auth = config.get_ari_auth()

        assert auth == ("admin", "secret")

    def test_webhook_validation_disabled(self, monkeypatch):
        """Test webhook validation defaults to disabled."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")

        config = AsteriskConfig()

        assert config.validate_webhook is False

    def test_webhook_validation_enabled(self, monkeypatch):
        """Test webhook validation can be enabled."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_ASTERISK_WEBHOOK", "true")
        monkeypatch.setenv("ASTERISK_WEBHOOK_SECRET", "secret")

        config = AsteriskConfig()

        assert config.validate_webhook is True
        assert config.webhook_secret == "secret"

    def test_state_file_default(self, monkeypatch):
        """Test Asterisk-specific state file default."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")

        config = AsteriskConfig()

        assert config.state_file == ".asterisk_adapter_state.json"
