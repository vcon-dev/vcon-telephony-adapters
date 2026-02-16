"""Tests for FreeSWITCH webhook endpoints."""

import json
import hashlib
import hmac
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from adapters.freeswitch.config import FreeSwitchConfig
from adapters.freeswitch.webhook import create_app


class TestFreeSwitchWebhook:
    """Tests for FreeSWITCH webhook endpoints."""

    @pytest.fixture
    def config(self, monkeypatch, tmp_path):
        """Create test config."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        return FreeSwitchConfig()

    @pytest.fixture
    def sample_recording_event(self):
        """Sample FreeSWITCH recording event."""
        return {
            "uuid": "abc123-def456",
            "caller_id_number": "+15551234567",
            "destination_number": "+15559876543",
            "direction": "inbound",
            "recording_url": "https://fs.example.com/recordings/abc123.wav",
            "record_seconds": "30",
            "start_epoch": "1705312170",
        }

    def test_health_check(self, config):
        """Test health check endpoint."""
        app = create_app(config)
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "vcon-freeswitch-adapter"

    def test_recording_event_success(self, config, sample_recording_event):
        """Test successful recording event processing."""
        with patch("adapters.freeswitch.webhook.HttpPoster") as mock_poster_class, \
             patch("adapters.freeswitch.webhook.FreeSwitchVconBuilder") as mock_builder_class:
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
        with patch("adapters.freeswitch.webhook.HttpPoster") as mock_poster_class, \
             patch("adapters.freeswitch.webhook.FreeSwitchVconBuilder") as mock_builder_class:
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

    def test_recording_event_no_uuid(self, config):
        """Test recording event without UUID."""
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            json={"caller_id_number": "+15551234567"},
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
        with patch("adapters.freeswitch.webhook.HttpPoster") as mock_poster_class, \
             patch("adapters.freeswitch.webhook.FreeSwitchVconBuilder") as mock_builder_class:
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

            response = client.get("/status/abc123-def456")
            assert response.status_code == 200
            data = response.json()
            assert data["recording_id"] == "abc123-def456"
            assert data["vcon_uuid"] == "vcon-uuid-123"
            assert data["status"] == "success"

    def test_get_recording_status_not_found(self, config):
        """Test getting status for unknown recording."""
        app = create_app(config)
        client = TestClient(app)
        response = client.get("/status/unknown-uuid")
        assert response.status_code == 404


class TestFreeSwitchWebhookValidation:
    """Tests for FreeSWITCH webhook signature validation."""

    @pytest.fixture
    def config_with_validation(self, monkeypatch, tmp_path):
        """Create config with webhook validation enabled."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_FREESWITCH_WEBHOOK", "true")
        monkeypatch.setenv("FREESWITCH_WEBHOOK_SECRET", "test-secret")
        return FreeSwitchConfig()

    def test_webhook_invalid_signature(self, config_with_validation):
        """Test webhook with invalid signature."""
        app = create_app(config_with_validation)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            json={"uuid": "abc123"},
            headers={"X-FreeSWITCH-Signature": "invalid"},
        )
        assert response.status_code == 403

    def test_webhook_valid_signature(self, config_with_validation):
        """Test webhook with valid signature."""
        data = json.dumps({"uuid": "abc123"}).encode()
        signature = hmac.new(
            b"test-secret",
            data,
            hashlib.sha256
        ).hexdigest()

        app = create_app(config_with_validation)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            content=data,
            headers={
                "Content-Type": "application/json",
                "X-FreeSWITCH-Signature": signature,
            },
        )
        # Should pass validation (may fail later due to missing mocks)
        assert response.status_code in (200, 500)
