"""Tests for Bandwidth webhook endpoints."""

import base64
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from adapters.bandwidth.config import BandwidthConfig
from adapters.bandwidth.webhook import create_app


class TestBandwidthWebhook:
    """Tests for Bandwidth webhook endpoints."""

    @pytest.fixture
    def config(self, monkeypatch, tmp_path):
        """Create test config."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_BANDWIDTH_WEBHOOK", "false")
        return BandwidthConfig()

    @pytest.fixture
    def sample_recording_event(self):
        """Sample Bandwidth recording complete event."""
        return {
            "eventType": "recordingComplete",
            "accountId": "123456",
            "applicationId": "app-789",
            "callId": "c-call-abc123",
            "recordingId": "r-rec-def456",
            "mediaUrl": "https://voice.bandwidth.com/recordings/media",
            "direction": "inbound",
            "from": "+15551234567",
            "to": "+15559876543",
            "startTime": "2024-01-15T10:29:30.000Z",
            "duration": "PT30S",
        }

    def test_health_check(self, config):
        """Test health check endpoint."""
        app = create_app(config)
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "vcon-bandwidth-adapter"

    def test_recording_event_success(self, config, sample_recording_event):
        """Test successful recording event processing."""
        with patch("adapters.bandwidth.webhook.HttpPoster") as mock_poster_class, \
             patch("adapters.bandwidth.webhook.BandwidthVconBuilder") as mock_builder_class:
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
        with patch("adapters.bandwidth.webhook.HttpPoster") as mock_poster_class, \
             patch("adapters.bandwidth.webhook.BandwidthVconBuilder") as mock_builder_class:
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
            json={"eventType": "initiate", "callId": "c-123"},
        )
        assert response.status_code == 200
        assert response.text == "OK"

    def test_recording_event_no_recording_id(self, config):
        """Test recording event without recording ID."""
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            json={"eventType": "recordingComplete", "callId": "c-123"},
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
        with patch("adapters.bandwidth.webhook.HttpPoster") as mock_poster_class, \
             patch("adapters.bandwidth.webhook.BandwidthVconBuilder") as mock_builder_class:
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

            response = client.get("/status/r-rec-def456")
            assert response.status_code == 200
            data = response.json()
            assert data["recording_id"] == "r-rec-def456"
            assert data["vcon_uuid"] == "vcon-uuid-123"

    def test_get_recording_status_not_found(self, config):
        """Test getting status for unknown recording."""
        app = create_app(config)
        client = TestClient(app)
        response = client.get("/status/unknown-id")
        assert response.status_code == 404


class TestBandwidthWebhookValidation:
    """Tests for Bandwidth webhook HTTP Basic authentication."""

    @pytest.fixture
    def config_with_validation(self, monkeypatch, tmp_path):
        """Create config with webhook validation enabled."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_BANDWIDTH_WEBHOOK", "true")
        monkeypatch.setenv("BANDWIDTH_WEBHOOK_USERNAME", "webhook-user")
        monkeypatch.setenv("BANDWIDTH_WEBHOOK_PASSWORD", "webhook-pass")
        return BandwidthConfig()

    def test_webhook_no_auth(self, config_with_validation):
        """Test webhook without authentication."""
        app = create_app(config_with_validation)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            json={"eventType": "recordingComplete", "recordingId": "r-123"},
        )
        assert response.status_code == 401

    def test_webhook_invalid_auth(self, config_with_validation):
        """Test webhook with invalid authentication."""
        app = create_app(config_with_validation)
        client = TestClient(app)
        credentials = base64.b64encode(b"wrong:credentials").decode()
        response = client.post(
            "/webhook/recording",
            json={"eventType": "recordingComplete", "recordingId": "r-123"},
            headers={"Authorization": f"Basic {credentials}"},
        )
        assert response.status_code == 401

    def test_webhook_valid_auth(self, config_with_validation):
        """Test webhook with valid authentication."""
        with patch("adapters.bandwidth.webhook.HttpPoster") as mock_poster_class, \
             patch("adapters.bandwidth.webhook.BandwidthVconBuilder") as mock_builder_class:
            mock_vcon = MagicMock()
            mock_vcon.uuid = "vcon-uuid-123"
            mock_builder = MagicMock()
            mock_builder.build.return_value = mock_vcon
            mock_builder_class.return_value = mock_builder

            mock_poster = MagicMock()
            mock_poster.post.return_value = True
            mock_poster_class.return_value = mock_poster

            app = create_app(config_with_validation)
            client = TestClient(app)

            credentials = base64.b64encode(b"webhook-user:webhook-pass").decode()
            response = client.post(
                "/webhook/recording",
                json={
                    "eventType": "recordingComplete",
                    "recordingId": "r-123",
                    "from": "+15551234567",
                    "to": "+15559876543",
                    "startTime": "2024-01-15T10:29:30.000Z",
                },
                headers={"Authorization": f"Basic {credentials}"},
            )
            assert response.status_code == 200
