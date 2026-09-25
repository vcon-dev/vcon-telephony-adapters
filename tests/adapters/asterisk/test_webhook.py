"""Tests for Asterisk webhook endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from adapters.asterisk.config import AsteriskConfig
from adapters.asterisk.webhook import create_app


class TestAsteriskWebhook:
    """Tests for Asterisk webhook endpoints."""

    @pytest.fixture
    def config(self, monkeypatch, tmp_path):
        """Create test config."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_ASTERISK_WEBHOOK", "false")
        return AsteriskConfig()

    @pytest.fixture
    def sample_recording_event(self):
        """Sample Asterisk ARI recording event."""
        return {
            "type": "RecordingFinished",
            "recording_name": "rec-abc123",
            "caller_id_num": "+15551234567",
            "connected_line_num": "+15559876543",
            "direction": "inbound",
            "duration": 30,
            "timestamp": "2024-01-15T10:29:30Z",
        }

    def test_health_check(self, config):
        """Test health check endpoint."""
        app = create_app(config)
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "vcon-asterisk-adapter"

    def test_recording_event_success(self, config, sample_recording_event):
        """Test successful recording event processing."""
        with (
            patch("adapters.asterisk.webhook.HttpPoster") as mock_poster_class,
            patch("adapters.asterisk.webhook.AsteriskVconBuilder") as mock_builder_class,
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
            patch("adapters.asterisk.webhook.HttpPoster") as mock_poster_class,
            patch("adapters.asterisk.webhook.AsteriskVconBuilder") as mock_builder_class,
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
            json={"type": "ChannelCreated", "channel_id": "123"},
        )
        assert response.status_code == 200
        assert response.text == "OK"

    def test_recording_event_no_name(self, config):
        """Test recording event without name."""
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            json={"type": "RecordingFinished"},
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
            patch("adapters.asterisk.webhook.HttpPoster") as mock_poster_class,
            patch("adapters.asterisk.webhook.AsteriskVconBuilder") as mock_builder_class,
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
        response = client.get("/status/unknown-name")
        assert response.status_code == 404


class TestAsteriskWebhookValidation:
    """Tests for Asterisk webhook signature validation and its fail-closed startup checks."""

    @pytest.fixture
    def config_with_validation(self, monkeypatch, tmp_path):
        """Create config with webhook validation enabled and a secret configured."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_ASTERISK_WEBHOOK", "true")
        monkeypatch.setenv("ASTERISK_WEBHOOK_SECRET", "test-secret")
        return AsteriskConfig()

    def test_missing_secret_refuses_to_start(self, monkeypatch, tmp_path):
        """Validation enabled with no secret raises at config construction."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_ASTERISK_WEBHOOK", "true")
        monkeypatch.delenv("ASTERISK_WEBHOOK_SECRET", raising=False)

        with pytest.raises(ValueError, match="ASTERISK_WEBHOOK_SECRET"):
            AsteriskConfig()

    def test_allow_unsigned_webhooks_opt_out(self, monkeypatch, tmp_path):
        """ALLOW_UNSIGNED_WEBHOOKS=true starts without a secret and accepts requests."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VALIDATE_ASTERISK_WEBHOOK", "true")
        monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
        monkeypatch.delenv("ASTERISK_WEBHOOK_SECRET", raising=False)

        config = AsteriskConfig()
        app = create_app(config)
        client = TestClient(app)

        response = client.post("/webhook/recording", json={"type": "RecordingFinished"})
        assert response.status_code == 200

    def test_webhook_missing_signature(self, config_with_validation):
        """Test webhook without a signature header is rejected."""
        app = create_app(config_with_validation)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            json={"type": "RecordingFinished", "recording_name": "rec-1"},
        )
        assert response.status_code == 403

    def test_webhook_invalid_signature(self, config_with_validation):
        """Test webhook with an invalid signature is rejected."""
        app = create_app(config_with_validation)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            json={"type": "RecordingFinished", "recording_name": "rec-1"},
            headers={"X-Asterisk-Signature": "invalid"},
        )
        assert response.status_code == 403

    def test_webhook_valid_signature(self, config_with_validation):
        """Test webhook with a valid HMAC signature is accepted."""
        import hashlib
        import hmac
        import json

        data = json.dumps({"type": "RecordingFinished", "recording_name": "rec-1"}).encode()
        signature = hmac.new(b"test-secret", data, hashlib.sha256).hexdigest()

        app = create_app(config_with_validation)
        client = TestClient(app)
        response = client.post(
            "/webhook/recording",
            content=data,
            headers={
                "Content-Type": "application/json",
                "X-Asterisk-Signature": signature,
            },
        )
        assert response.status_code == 200
