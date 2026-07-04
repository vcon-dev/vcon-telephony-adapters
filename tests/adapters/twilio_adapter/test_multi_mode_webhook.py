"""Tests for Twilio multi-mode webhook endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from adapters.twilio.config import TwilioConfig
from adapters.twilio.webhook import create_app

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "twilio"


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("VALIDATE_TWILIO_SIGNATURE", "false")
    return TwilioConfig()


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class TestMultiModeWebhooks:
    @patch("adapters.twilio.webhook.HttpPoster")
    def test_messaging_webhook(self, mock_poster_class, config):
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(config)
        client = TestClient(app)
        form = _load("sms_inbound.json")

        response = client.post("/webhook/messaging", data=form)
        assert response.status_code == 200
        assert response.text == "OK"
        mock_poster.post.assert_called_once()

    @patch("adapters.twilio.webhook.HttpPoster")
    def test_voice_status_webhook(self, mock_poster_class, config):
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(config)
        client = TestClient(app)
        form = _load("voice_status_no_answer.json")

        response = client.post("/webhook/voice/status", data=form)
        assert response.status_code == 200
        mock_poster.post.assert_called_once()

    @patch("adapters.twilio.webhook.HttpPoster")
    def test_fax_webhook(self, mock_poster_class, config):
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        with patch("adapters.twilio.webhook.TwilioFaxBuilder") as mock_builder_class:
            mock_vcon = MagicMock()
            mock_vcon.uuid = "vcon-fax-1"
            mock_builder = MagicMock()
            mock_builder.should_process.return_value = True
            mock_builder.build.return_value = mock_vcon
            mock_builder_class.return_value = mock_builder

            app = create_app(config)
            client = TestClient(app)
            response = client.post("/webhook/fax", data=_load("fax_received.json"))
            assert response.status_code == 200

    @patch("adapters.twilio.webhook.HttpPoster")
    def test_conversations_json_webhook(self, mock_poster_class, config):
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(config)
        client = TestClient(app)
        payload = _load("conversations_message.json")

        response = client.post("/webhook/conversations", json=payload)
        assert response.status_code == 200
        mock_poster.post.assert_called_once()

    def test_health_lists_modes(self, config):
        app = create_app(config)
        client = TestClient(app)
        data = client.get("/health").json()
        assert data["modes"]["messaging"] is True
        assert data["modes"]["fax"] is True
