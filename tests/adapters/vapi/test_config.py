"""Tests for VAPI configuration."""

import pytest

from adapters.vapi.config import VapiConfig


class TestVapiConfig:
    def test_minimal_config(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_VAPI_WEBHOOK", "false")
        config = VapiConfig()
        assert config.conserver_url == "https://conserver.example.com/vcon"
        assert config.customer_party_name == "Customer"
        assert config.agent_party_name == "AI Agent"

    def test_missing_conserver_url(self, monkeypatch):
        monkeypatch.delenv("CONSERVER_URL", raising=False)
        with pytest.raises(ValueError, match="CONSERVER_URL"):
            VapiConfig()

    def test_webhook_validation_enabled_by_default(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "shh")
        config = VapiConfig()
        assert config.validate_webhook is True

    def test_missing_secret_refuses_to_start(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.delenv("VAPI_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("ALLOW_UNSIGNED_WEBHOOKS", raising=False)
        with pytest.raises(ValueError, match="VAPI_WEBHOOK_SECRET"):
            VapiConfig()

    def test_missing_secret_allowed_with_opt_out(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.delenv("VAPI_WEBHOOK_SECRET", raising=False)
        monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
        config = VapiConfig()
        assert config.validate_webhook is True
        assert config.webhook_secret is None

    def test_custom_party_names(self, monkeypatch):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("VALIDATE_VAPI_WEBHOOK", "false")
        monkeypatch.setenv("VAPI_CUSTOMER_NAME", "Caller")
        monkeypatch.setenv("VAPI_AGENT_NAME", "Bot")
        config = VapiConfig()
        assert config.customer_party_name == "Caller"
        assert config.agent_party_name == "Bot"
