"""Tests for the VAPI webhook endpoint, including signature validation."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from adapters.vapi.config import VapiConfig
from adapters.vapi.webhook import create_app


def make_message():
    return {
        "message": {
            "type": "end-of-call-report",
            "call": {"id": "call-abc"},
            "artifact": {"messages": [{"role": "user", "message": "hi", "time": 1}]},
        }
    }


class TestVapiWebhook:
    @pytest.fixture
    def config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "topsecret")
        return VapiConfig()

    @pytest.fixture
    def unvalidated_config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state2.json"))
        monkeypatch.setenv("VALIDATE_VAPI_WEBHOOK", "false")
        return VapiConfig()

    def test_health_check(self, config):
        app = create_app(config)
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == "vcon-vapi-adapter"

    def test_valid_secret_accepted(self, config):
        with (
            patch("adapters.vapi.webhook.HttpPoster") as mock_poster_class,
            patch("adapters.vapi.webhook.VapiVconBuilder") as mock_builder_class,
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
            response = client.post(
                "/vapi", json=make_message(), headers={"x-vapi-secret": "topsecret"}
            )
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

    def test_bad_secret_rejected(self, config):
        app = create_app(config)
        client = TestClient(app)
        response = client.post("/vapi", json=make_message(), headers={"x-vapi-secret": "wrong"})
        assert response.status_code == 401

    def test_missing_secret_header_rejected(self, config):
        app = create_app(config)
        client = TestClient(app)
        response = client.post("/vapi", json=make_message())
        assert response.status_code == 401

    def test_missing_secret_refuses_to_start(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
        monkeypatch.setenv("STATE_FILE", str(tmp_path / "state3.json"))
        monkeypatch.delenv("VAPI_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("ALLOW_UNSIGNED_WEBHOOKS", raising=False)
        with pytest.raises(ValueError, match="VAPI_WEBHOOK_SECRET"):
            VapiConfig()

    def test_opt_out_accepts_unsigned(self, unvalidated_config):
        with (
            patch("adapters.vapi.webhook.HttpPoster") as mock_poster_class,
            patch("adapters.vapi.webhook.VapiVconBuilder") as mock_builder_class,
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
            response = client.post("/vapi", json=make_message())
            assert response.status_code == 200

    def test_ignored_message_type(self, config):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/vapi",
            json={"message": {"type": "status-update", "call": {"id": "x"}}},
            headers={"x-vapi-secret": "topsecret"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ignored"

    def test_missing_message_key(self, config):
        app = create_app(config)
        client = TestClient(app)
        response = client.post("/vapi", json={}, headers={"x-vapi-secret": "topsecret"})
        assert response.status_code == 422
