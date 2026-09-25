"""Tests for Telnyx webhook endpoints."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from adapters.telnyx.config import TelnyxConfig
from adapters.telnyx.webhook import create_app


class TestTelnyxWebhook:
    """Tests for Telnyx webhook endpoints."""

    @pytest.fixture
    def config(self, monkeypatch, tmp_path):
        """Create test config."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_TELNYX_WEBHOOK", "false")
        return TelnyxConfig()

    @pytest.fixture
    def sample_recording_event(self):
        """Sample Telnyx recording webhook event."""
        return {
            "data": {
                "event_type": "call.recording.saved",
                "id": "event-123",
                "occurred_at": "2024-01-15T10:30:00Z",
                "payload": {
                    "recording_id": "rec-abc123",
                    "call_session_id": "session-xyz",
                    "from": "+15551234567",
                    "to": "+15559876543",
                    "direction": "incoming",
                    "duration_millis": 30000,
                    "recording_urls": {"wav": "https://api.telnyx.com/recordings/rec-abc123.wav"},
                    "start_time": "2024-01-15T10:29:30Z",
                },
            }
        }

    def test_health_check(self, config):
        """Test health check endpoint."""
        app = create_app(config)
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "vcon-telnyx-adapter"

    def test_recording_event_success(self, config, sample_recording_event):
        """Test successful recording event processing."""
        with (
            patch("adapters.telnyx.webhook.HttpPoster") as mock_poster_class,
            patch("adapters.telnyx.webhook.TelnyxVconBuilder") as mock_builder_class,
        ):
            mock_vcon = MagicMock()
            mock_vcon.uuid = "vcon-uuid-123"
            mock_builder = MagicMock()
            mock_builder.build.return_value = mock_vcon
            mock_builder_class.return_value = mock_builder

            mock_poster = MagicMock()
            mock_poster.post.return_value = True
            mock_poster_class.return_value = mock_poster

            app = create_app(config)
            client = TestClient(app)

            response = client.post(
                "/webhook/recording",
                json=sample_recording_event,
            )

            assert response.status_code == 200
            assert response.text == "OK"

    def test_recording_event_duplicate(self, config, sample_recording_event):
        """Test duplicate recording event handling."""
        with (
            patch("adapters.telnyx.webhook.HttpPoster") as mock_poster_class,
            patch("adapters.telnyx.webhook.TelnyxVconBuilder") as mock_builder_class,
        ):
            mock_vcon = MagicMock()
            mock_vcon.uuid = "vcon-uuid-123"
            mock_builder = MagicMock()
            mock_builder.build.return_value = mock_vcon
            mock_builder_class.return_value = mock_builder

            mock_poster = MagicMock()
            mock_poster.post.return_value = True
            mock_poster_class.return_value = mock_poster

            app = create_app(config)
            client = TestClient(app)

            response1 = client.post("/webhook/recording", json=sample_recording_event)
            assert response1.status_code == 200

            response2 = client.post("/webhook/recording", json=sample_recording_event)
            assert response2.status_code == 200

    def test_recording_event_wrong_type(self, config):
        """Test ignoring non-recording events."""
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            json={
                "data": {"event_type": "call.initiated", "payload": {"call_control_id": "ctrl-123"}}
            },
        )
        assert response.status_code == 200
        assert response.text == "OK"

    def test_recording_event_invalid_json(self, config):
        """Test recording event with invalid JSON."""
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            content="not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_get_recording_status(self, config, sample_recording_event):
        """Test getting recording status."""
        with (
            patch("adapters.telnyx.webhook.HttpPoster") as mock_poster_class,
            patch("adapters.telnyx.webhook.TelnyxVconBuilder") as mock_builder_class,
        ):
            mock_vcon = MagicMock()
            mock_vcon.uuid = "vcon-uuid-123"
            mock_builder = MagicMock()
            mock_builder.build.return_value = mock_vcon
            mock_builder_class.return_value = mock_builder

            mock_poster = MagicMock()
            mock_poster.post.return_value = True
            mock_poster_class.return_value = mock_poster

            app = create_app(config)
            client = TestClient(app)

            client.post("/webhook/recording", json=sample_recording_event)

            response = client.get("/status/rec-abc123")
            assert response.status_code == 200
            data = response.json()
            assert data["recording_id"] == "rec-abc123"
            assert data["vcon_uuid"] == "vcon-uuid-123"

    def test_get_recording_status_not_found(self, config):
        """Test getting status for unknown recording."""
        app = create_app(config)
        client = TestClient(app)
        response = client.get("/status/unknown-id")
        assert response.status_code == 404


class TestTelnyxWebhookValidation:
    """Tests for Telnyx webhook signature validation."""

    @pytest.fixture
    def keypair(self):
        """A fresh Ed25519 keypair for signing test payloads."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        private_key = Ed25519PrivateKey.generate()
        public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return private_key, base64.b64encode(public_bytes).decode()

    @pytest.fixture
    def config_with_validation(self, monkeypatch, tmp_path, keypair):
        """Create config with webhook validation enabled and secrets configured."""
        _, public_key_b64 = keypair
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_TELNYX_WEBHOOK", "true")
        monkeypatch.setenv("TELNYX_PUBLIC_KEY", public_key_b64)
        monkeypatch.setenv("TELNYX_TEXML_CALLBACK_TOKEN", "texml-token")
        return TelnyxConfig()

    def test_webhook_missing_headers(self, config_with_validation):
        """Test webhook without signature headers is rejected."""
        app = create_app(config_with_validation)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            json={"data": {"event_type": "call.recording.saved", "payload": {}}},
        )
        assert response.status_code == 403

    def test_webhook_invalid_signature(self, config_with_validation):
        """Test webhook with invalid signature is rejected."""
        app = create_app(config_with_validation)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            json={"data": {"event_type": "call.recording.saved", "payload": {}}},
            headers={
                "Telnyx-Signature-Ed25519": "invalid",
                "Telnyx-Timestamp": "1705312170",
            },
        )
        assert response.status_code == 403

    def test_webhook_valid_signature_accepted(self, config_with_validation, keypair):
        """A correctly signed `timestamp|body` payload is accepted."""
        private_key, _ = keypair
        body = json.dumps({"data": {"event_type": "call.initiated", "payload": {}}}).encode()
        timestamp = "1705312170"
        signed_payload = f"{timestamp}|".encode() + body
        signature = base64.b64encode(private_key.sign(signed_payload)).decode()

        app = create_app(config_with_validation)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Telnyx-Signature-Ed25519": signature,
                "Telnyx-Timestamp": timestamp,
            },
        )
        assert response.status_code == 200

    def test_webhook_wrong_separator_is_rejected(self, config_with_validation, keypair):
        """A signature computed over the old `timestamp.body` payload no longer verifies."""
        private_key, _ = keypair
        body = json.dumps({"data": {"event_type": "call.initiated", "payload": {}}}).encode()
        timestamp = "1705312170"
        # The old (wrong) separator: this must NOT verify against the fixed code.
        signed_payload = f"{timestamp}.".encode() + body
        signature = base64.b64encode(private_key.sign(signed_payload)).decode()

        app = create_app(config_with_validation)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Telnyx-Signature-Ed25519": signature,
                "Telnyx-Timestamp": timestamp,
            },
        )
        assert response.status_code == 403


class TestTelnyxWebhookFailClosed:
    """Webhook validation refuses to start unless it can actually check something."""

    def test_missing_public_key_refuses_to_start(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_TELNYX_WEBHOOK", "true")
        monkeypatch.setenv("TELNYX_TEXML_CALLBACK_TOKEN", "texml-token")
        monkeypatch.delenv("TELNYX_PUBLIC_KEY", raising=False)

        with pytest.raises(ValueError, match="TELNYX_PUBLIC_KEY"):
            TelnyxConfig()

    def test_allow_unsigned_webhooks_opt_out(self, monkeypatch, tmp_path):
        """ALLOW_UNSIGNED_WEBHOOKS=true starts without a public key and accepts requests."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_TELNYX_WEBHOOK", "true")
        monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
        monkeypatch.delenv("TELNYX_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("TELNYX_TEXML_CALLBACK_TOKEN", raising=False)

        config = TelnyxConfig()
        app = create_app(config)
        client = TestClient(app)

        response = client.post(
            "/webhook/recording",
            json={"data": {"event_type": "call.recording.saved", "payload": {}}},
        )
        assert response.status_code == 200

    def test_missing_cryptography_fails_closed(self, monkeypatch, tmp_path):
        """If `cryptography` cannot be imported, a signed webhook is rejected, not skipped."""
        import sys

        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_TELNYX_WEBHOOK", "true")
        monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")  # only to get past config startup
        monkeypatch.setenv("TELNYX_PUBLIC_KEY", base64.b64encode(b"x" * 32).decode())
        config = TelnyxConfig()
        # Simulate the library being unavailable at request time, independent of
        # whatever config-construction-time check exists.
        monkeypatch.setattr(config, "allow_unsigned_webhooks", False)
        monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.asymmetric.ed25519", None)

        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            json={"data": {"event_type": "call.recording.saved", "payload": {}}},
            headers={
                "Telnyx-Signature-Ed25519": base64.b64encode(b"y" * 64).decode(),
                "Telnyx-Timestamp": "1705312170",
            },
        )
        assert response.status_code == 403


class TestTelnyxTexmlCallbackToken:
    """The TeXML recording callback has no ed25519 signature; it is token-gated instead."""

    @pytest.fixture
    def config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_TELNYX_WEBHOOK", "true")
        monkeypatch.setenv("TELNYX_PUBLIC_KEY", "irrelevant-for-this-test")
        monkeypatch.setenv("TELNYX_TEXML_CALLBACK_TOKEN", "texml-secret-token")
        return TelnyxConfig()

    def test_missing_token_rejected(self, config):
        app = create_app(config)
        client = TestClient(app)
        response = client.post("/webhook/texml-recording", data={"RecordingStatus": "completed"})
        assert response.status_code == 403

    def test_wrong_token_rejected(self, config):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/webhook/texml-recording?token=wrong",
            data={"RecordingStatus": "completed"},
        )
        assert response.status_code == 403

    def test_correct_token_accepted(self, config):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/webhook/texml-recording?token=texml-secret-token",
            data={"RecordingStatus": "in-progress"},
        )
        # Token accepted; a non-completed status is a no-op "OK", not a rejection.
        assert response.status_code == 200

    def test_missing_token_config_refuses_to_start(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_TELNYX_WEBHOOK", "true")
        monkeypatch.setenv("TELNYX_PUBLIC_KEY", "irrelevant-for-this-test")
        monkeypatch.delenv("TELNYX_TEXML_CALLBACK_TOKEN", raising=False)

        from adapters.telnyx.config import TelnyxConfig as Cfg

        with pytest.raises(ValueError, match="TELNYX_TEXML_CALLBACK_TOKEN"):
            Cfg()
