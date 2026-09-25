"""Tests for SignalWire configuration."""

import pytest

from adapters.signalwire.config import SignalWireConfig


class TestSignalWireConfig:
    def _set_required(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("SIGNALWIRE_PROJECT_ID", "PJ123")
        monkeypatch.setenv("SIGNALWIRE_AUTH_TOKEN", "TOKEN123")
        monkeypatch.setenv("SIGNALWIRE_SPACE_URL", "https://example.signalwire.com")

    def test_minimal_config(self, monkeypatch):
        self._set_required(monkeypatch)
        config = SignalWireConfig()
        assert config.project_id == "PJ123"
        assert config.poll_interval_seconds == 300
        assert config.retention_days == 30

    def test_missing_conserver_url(self, monkeypatch):
        monkeypatch.delenv("CONSERVER_URL", raising=False)
        monkeypatch.setenv("SIGNALWIRE_PROJECT_ID", "PJ123")
        monkeypatch.setenv("SIGNALWIRE_AUTH_TOKEN", "TOKEN123")
        monkeypatch.setenv("SIGNALWIRE_SPACE_URL", "https://example.signalwire.com")
        with pytest.raises(ValueError, match="CONSERVER_URL"):
            SignalWireConfig()

    @pytest.mark.parametrize(
        "missing_var",
        ["SIGNALWIRE_PROJECT_ID", "SIGNALWIRE_AUTH_TOKEN", "SIGNALWIRE_SPACE_URL"],
    )
    def test_missing_required_signalwire_vars(self, monkeypatch, missing_var):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("SIGNALWIRE_PROJECT_ID", "PJ123")
        monkeypatch.setenv("SIGNALWIRE_AUTH_TOKEN", "TOKEN123")
        monkeypatch.setenv("SIGNALWIRE_SPACE_URL", "https://example.signalwire.com")
        monkeypatch.delenv(missing_var, raising=False)
        with pytest.raises(ValueError, match="SIGNALWIRE_"):
            SignalWireConfig()

    def test_no_webhook_validation_setting(self, monkeypatch):
        """SignalWire is a poller: there is no VALIDATE_*_WEBHOOK concept."""
        self._set_required(monkeypatch)
        config = SignalWireConfig()
        assert not hasattr(config, "validate_webhook")

    def test_custom_poll_interval(self, monkeypatch):
        self._set_required(monkeypatch)
        monkeypatch.setenv("SIGNALWIRE_POLL_INTERVAL_SECONDS", "60")
        config = SignalWireConfig()
        assert config.poll_interval_seconds == 60

    def test_api_base_and_auth(self, monkeypatch):
        self._set_required(monkeypatch)
        config = SignalWireConfig()
        assert config.api_base() == (
            "https://example.signalwire.com/api/laml/2010-04-01/Accounts/PJ123"
        )
        assert config.api_auth() == ("PJ123", "TOKEN123")
