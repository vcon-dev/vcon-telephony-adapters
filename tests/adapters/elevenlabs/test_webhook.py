"""Tests for the ElevenLabs webhook endpoint, including signature validation."""

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from adapters.elevenlabs.config import ElevenLabsConfig
from adapters.elevenlabs.webhook import create_app

SECRET = "topsecret"


def sign(body: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    signed_payload = f"{ts}.".encode() + body
    signature = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v0={signature}"


def make_event():
    return {
        "type": "post_call_transcription",
        "event_timestamp": int(time.time()),
        "data": {
            "agent_id": "agent-1",
            "conversation_id": "conv-abc",
            "transcript": [{"role": "user", "message": "hi", "time_in_call_secs": 0}],
        },
    }


class TestElevenLabsWebhook:
    @pytest.fixture
    def config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", SECRET)
        return ElevenLabsConfig()

    @pytest.fixture
    def unvalidated_config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state2.json"))
        monkeypatch.setenv("VALIDATE_ELEVENLABS_WEBHOOK", "false")
        return ElevenLabsConfig()

    def test_health_check(self, config):
        app = create_app(config)
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == "vcon-elevenlabs-adapter"

    def test_valid_signature_accepted(self, config):
        with (
            patch("adapters.elevenlabs.webhook.HttpPoster") as mock_poster_class,
            patch("adapters.elevenlabs.webhook.ElevenLabsVconBuilder") as mock_builder_class,
        ):
            mock_vcon = MagicMock()
            mock_vcon.uuid = "vcon-uuid-1"
            mock_builder = MagicMock()
            mock_builder.build.return_value = mock_vcon
            mock_builder_class.return_value = mock_builder

            mock_poster = MagicMock()
            mock_poster.post.return_value = True
            mock_poster_class.return_value = mock_poster

            app = create_app(config)
            client = TestClient(app)

            body = json.dumps(make_event()).encode()
            response = client.post(
                "/webhook/post-call",
                content=body,
                headers={
                    "content-type": "application/json",
                    "elevenlabs-signature": sign(body),
                },
            )
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

    def test_bad_signature_rejected(self, config):
        app = create_app(config)
        client = TestClient(app)
        body = json.dumps(make_event()).encode()
        response = client.post(
            "/webhook/post-call",
            content=body,
            headers={
                "content-type": "application/json",
                "elevenlabs-signature": sign(body, secret="wrong-secret"),
            },
        )
        assert response.status_code == 401

    def test_missing_signature_rejected(self, config):
        app = create_app(config)
        client = TestClient(app)
        body = json.dumps(make_event()).encode()
        response = client.post(
            "/webhook/post-call", content=body, headers={"content-type": "application/json"}
        )
        assert response.status_code == 401

    def test_stale_timestamp_rejected(self, config):
        app = create_app(config)
        client = TestClient(app)
        body = json.dumps(make_event()).encode()
        old_timestamp = int(time.time()) - 999999
        response = client.post(
            "/webhook/post-call",
            content=body,
            headers={
                "content-type": "application/json",
                "elevenlabs-signature": sign(body, timestamp=old_timestamp),
            },
        )
        assert response.status_code == 401

    def test_future_timestamp_beyond_window_rejected(self, config):
        app = create_app(config)
        client = TestClient(app)
        body = json.dumps(make_event()).encode()
        future_timestamp = int(time.time()) + 999999
        response = client.post(
            "/webhook/post-call",
            content=body,
            headers={
                "content-type": "application/json",
                "elevenlabs-signature": sign(body, timestamp=future_timestamp),
            },
        )
        assert response.status_code == 401

    def test_non_numeric_timestamp_rejected(self, config):
        app = create_app(config)
        client = TestClient(app)
        body = json.dumps(make_event()).encode()
        # Sign over the literal "t=not-a-number" header value so the HMAC
        # itself checks out; only the timestamp-freshness check should reject.
        raw_ts = "not-a-number"
        signed_payload = f"{raw_ts}.".encode() + body
        digest = hmac.new(SECRET.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        response = client.post(
            "/webhook/post-call",
            content=body,
            headers={
                "content-type": "application/json",
                "elevenlabs-signature": f"t={raw_ts},v0={digest}",
            },
        )
        assert response.status_code == 401

    def test_missing_secret_refuses_to_start(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state3.json"))
        monkeypatch.delenv("ELEVENLABS_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("ALLOW_UNSIGNED_WEBHOOKS", raising=False)
        with pytest.raises(ValueError, match="ELEVENLABS_WEBHOOK_SECRET"):
            ElevenLabsConfig()

    def test_opt_out_accepts_unsigned(self, unvalidated_config):
        with (
            patch("adapters.elevenlabs.webhook.HttpPoster") as mock_poster_class,
            patch("adapters.elevenlabs.webhook.ElevenLabsVconBuilder") as mock_builder_class,
        ):
            mock_vcon = MagicMock()
            mock_vcon.uuid = "vcon-uuid-2"
            mock_builder = MagicMock()
            mock_builder.build.return_value = mock_vcon
            mock_builder_class.return_value = mock_builder

            mock_poster = MagicMock()
            mock_poster.post.return_value = True
            mock_poster_class.return_value = mock_poster

            app = create_app(unvalidated_config)
            client = TestClient(app)
            body = json.dumps(make_event()).encode()
            response = client.post(
                "/webhook/post-call", content=body, headers={"content-type": "application/json"}
            )
            assert response.status_code == 200


FIXED_NOW = 1_705_312_170


class TestElevenLabsWebhookTimestampBoundary:
    """Deterministic boundary checks against a frozen clock, exercising
    core.webhook_security.is_timestamp_fresh via the real HMAC + freshness path."""

    @pytest.fixture(autouse=True)
    def frozen_time(self, monkeypatch):
        monkeypatch.setattr("core.webhook_security.time.time", lambda: float(FIXED_NOW))

    @pytest.fixture
    def config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state-boundary.json"))
        monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", SECRET)
        monkeypatch.setenv("WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", "300")
        monkeypatch.delenv("ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS", raising=False)
        return ElevenLabsConfig()

    def _post(self, config, timestamp):
        app = create_app(config)
        client = TestClient(app)
        body = json.dumps(make_event()).encode()
        return client.post(
            "/webhook/post-call",
            content=body,
            headers={
                "content-type": "application/json",
                "elevenlabs-signature": sign(body, timestamp=timestamp),
            },
        )

    def test_fresh_timestamp_accepted(self, config):
        response = self._post(config, FIXED_NOW)
        assert response.status_code == 200

    def test_boundary_exactly_at_tolerance_accepted(self, config):
        response = self._post(config, FIXED_NOW - 300)
        assert response.status_code == 200

    def test_one_second_past_tolerance_rejected(self, config):
        response = self._post(config, FIXED_NOW - 301)
        assert response.status_code == 401

    def test_per_platform_override_applies(self, monkeypatch, tmp_path):
        """ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS wins over the shared value: a
        timestamp fresh under the wide shared window is rejected under a
        tighter per-platform override."""
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state-override.json"))
        monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", SECRET)
        monkeypatch.setenv("WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", "300")
        monkeypatch.setenv("ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS", "10")
        config = ElevenLabsConfig()
        assert config.webhook_timestamp_tolerance_seconds == 10

        response = self._post(config, FIXED_NOW - 60)
        assert response.status_code == 401
