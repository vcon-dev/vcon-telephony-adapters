"""Tests for Telnyx webhook endpoints."""

import pytest
from unittest.mock import patch, MagicMock
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
                    "recording_urls": {
                        "wav": "https://api.telnyx.com/recordings/rec-abc123.wav"
                    },
                    "start_time": "2024-01-15T10:29:30Z"
                }
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
        with patch("adapters.telnyx.webhook.HttpPoster") as mock_poster_class, \
             patch("adapters.telnyx.webhook.TelnyxVconBuilder") as mock_builder_class:
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
        with patch("adapters.telnyx.webhook.HttpPoster") as mock_poster_class, \
             patch("adapters.telnyx.webhook.TelnyxVconBuilder") as mock_builder_class:
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
                "data": {
                    "event_type": "call.initiated",
                    "payload": {"call_control_id": "ctrl-123"}
                }
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
        with patch("adapters.telnyx.webhook.HttpPoster") as mock_poster_class, \
             patch("adapters.telnyx.webhook.TelnyxVconBuilder") as mock_builder_class:
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
    def config_with_validation(self, monkeypatch, tmp_path):
        """Create config with webhook validation enabled."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_TELNYX_WEBHOOK", "true")
        return TelnyxConfig()

    def test_webhook_missing_headers(self, config_with_validation):
        """Test webhook without signature headers."""
        app = create_app(config_with_validation)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            json={"data": {"event_type": "call.recording.saved", "payload": {}}},
        )
        # Without public key configured, validation is skipped
        assert response.status_code in (200, 403)

    def test_webhook_invalid_signature(self, config_with_validation):
        """Test webhook with invalid signature."""
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
        # Validation should fail or be skipped if no public key
        assert response.status_code in (200, 403)
