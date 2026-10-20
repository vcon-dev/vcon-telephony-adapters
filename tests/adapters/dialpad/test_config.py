"""Tests for Dialpad configuration."""

import pytest

from adapters.dialpad.client import DialpadClient
from adapters.dialpad.config import DialpadConfig

ENV_VARS = [
    "DIALPAD_API_KEY",
    "DIALPAD_API_BASE",
    "DIALPAD_WEBHOOK_SECRET",
    "VALIDATE_DIALPAD_SIGNATURE",
    "DIALPAD_WEBHOOK_TOLERANCE_SECONDS",
    "WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS",
    "DIALPAD_TRIGGER_STATE",
    "DIALPAD_INCLUDE_TRANSCRIPT",
    "DIALPAD_INCLUDE_RECAP",
    "DIALPAD_INCLUDE_ADMIN_RECORDINGS",
    "ALLOW_UNSIGNED_WEBHOOKS",
    "STATE_FILE",
]


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
    monkeypatch.setenv("DIALPAD_API_KEY", "test-key")
    monkeypatch.setenv("DIALPAD_WEBHOOK_SECRET", "shh")


class TestDialpadConfig:
    def test_defaults(self):
        config = DialpadConfig()
        assert config.state_file == ".dialpad_adapter_state.json"
        assert config.api_base == "https://dialpad.com"
        assert config.validate_webhook is True
        assert config.webhook_timestamp_tolerance_seconds == 3600
        assert config.trigger_state == "recording"
        assert config.include_transcript is True
        assert config.include_recap is True
        assert config.include_admin_recordings is False

    def test_api_key_required(self, monkeypatch):
        monkeypatch.delenv("DIALPAD_API_KEY")
        with pytest.raises(ValueError, match="DIALPAD_API_KEY"):
            DialpadConfig()

    def test_api_base_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("DIALPAD_API_BASE", "https://sandbox.dialpad.com/")
        assert DialpadConfig().api_base == "https://sandbox.dialpad.com"

    def test_missing_secret_refuses_to_start(self, monkeypatch):
        monkeypatch.delenv("DIALPAD_WEBHOOK_SECRET")
        with pytest.raises(ValueError, match="DIALPAD_WEBHOOK_SECRET"):
            DialpadConfig()

    def test_missing_secret_allowed_with_opt_out(self, monkeypatch):
        monkeypatch.delenv("DIALPAD_WEBHOOK_SECRET")
        monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
        assert DialpadConfig().webhook_secret is None

    def test_missing_secret_ok_when_validation_off(self, monkeypatch):
        monkeypatch.delenv("DIALPAD_WEBHOOK_SECRET")
        monkeypatch.setenv("VALIDATE_DIALPAD_SIGNATURE", "false")
        assert DialpadConfig().validate_webhook is False

    def test_per_platform_tolerance_wins(self, monkeypatch):
        monkeypatch.setenv("DIALPAD_WEBHOOK_TOLERANCE_SECONDS", "60")
        monkeypatch.setenv("WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", "45")
        assert DialpadConfig().webhook_timestamp_tolerance_seconds == 60

    def test_shared_tolerance_applies_without_override(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", "45")
        assert DialpadConfig().webhook_timestamp_tolerance_seconds == 45

    def test_invalid_tolerance_rejected(self, monkeypatch):
        monkeypatch.setenv("DIALPAD_WEBHOOK_TOLERANCE_SECONDS", "0")
        with pytest.raises(ValueError, match="DIALPAD_WEBHOOK_TOLERANCE_SECONDS"):
            DialpadConfig()

    def test_trigger_state_call_transcription(self, monkeypatch):
        monkeypatch.setenv("DIALPAD_TRIGGER_STATE", "call_transcription")
        assert DialpadConfig().trigger_state == "call_transcription"

    def test_invalid_trigger_state_rejected(self, monkeypatch):
        monkeypatch.setenv("DIALPAD_TRIGGER_STATE", "hangup")
        with pytest.raises(ValueError, match="DIALPAD_TRIGGER_STATE"):
            DialpadConfig()

    def test_feature_flags(self, monkeypatch):
        monkeypatch.setenv("DIALPAD_INCLUDE_TRANSCRIPT", "false")
        monkeypatch.setenv("DIALPAD_INCLUDE_RECAP", "false")
        monkeypatch.setenv("DIALPAD_INCLUDE_ADMIN_RECORDINGS", "true")
        config = DialpadConfig()
        assert config.include_transcript is False
        assert config.include_recap is False
        assert config.include_admin_recordings is True

    def test_build_client(self):
        client = DialpadConfig().build_client()
        assert isinstance(client, DialpadClient)
        assert client.api_base == "https://dialpad.com"
        assert "test-key" not in repr(client)
