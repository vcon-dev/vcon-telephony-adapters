"""Tests for the Dialpad webhook endpoint, including JWT validation."""

import json
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from adapters.dialpad.builder import DialpadBuildError
from adapters.dialpad.config import DialpadConfig
from adapters.dialpad.webhook import MAX_ATTEMPTS, create_app

SECRET = "topsecret"


def now_ms(offset_seconds: float = 0) -> int:
    return int((time.time() + offset_seconds) * 1000)


def make_event(state="recording", call_id=1234, timestamp="default"):
    event = {"state": state, "call_id": call_id}
    if timestamp == "default":
        event["event_timestamp"] = now_ms()
    elif timestamp is not None:
        event["event_timestamp"] = timestamp
    return event


def sign(event, secret=SECRET, algorithm="HS256"):
    return jwt.encode(event, secret, algorithm=algorithm)


def base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("DIALPAD_API_KEY", "test-api-key")


@pytest.fixture
def config(monkeypatch, tmp_path):
    base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DIALPAD_WEBHOOK_SECRET", SECRET)
    return DialpadConfig()


@pytest.fixture
def unsigned_config(monkeypatch, tmp_path):
    base_env(monkeypatch, tmp_path)
    monkeypatch.delenv("DIALPAD_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
    return DialpadConfig()


@pytest.fixture
def mocks():
    """Patch builder and poster; yield (builder, poster)."""
    with (
        patch("adapters.dialpad.webhook.HttpPoster") as poster_class,
        patch("adapters.dialpad.webhook.DialpadVconBuilder") as builder_class,
    ):
        vcon = MagicMock()
        vcon.uuid = "vcon-uuid-1"
        builder = MagicMock()
        builder.build.return_value = vcon
        builder_class.return_value = builder
        poster = MagicMock()
        poster.post.return_value = True
        poster_class.return_value = poster
        yield builder, poster


def post_jwt(client, token):
    return client.post("/webhook/call-event", content=token)


def post_json(client, event):
    return client.post("/webhook/call-event", content=json.dumps(event))


class TestSignature:
    def test_valid_jwt_builds(self, config, mocks):
        builder, poster = mocks
        client = TestClient(create_app(config))
        response = post_jwt(client, sign(make_event()))
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "uuid": "vcon-uuid-1"}
        builder.build.assert_called_once()
        poster.post.assert_called_once()

    def test_wrong_secret_rejected(self, config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(config))
        assert post_jwt(client, sign(make_event(), secret="other")).status_code == 401
        builder.build.assert_not_called()

    def test_alg_none_rejected(self, config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(config))
        token = jwt.encode(make_event(), None, algorithm="none")
        assert post_jwt(client, token).status_code == 401
        builder.build.assert_not_called()

    def test_other_hmac_algorithm_rejected(self, config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(config))
        token = sign(make_event(), algorithm="HS512")
        assert post_jwt(client, token).status_code == 401
        builder.build.assert_not_called()

    def test_plain_json_rejected_when_secret_configured(self, config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(config))
        assert post_json(client, make_event()).status_code == 401
        builder.build.assert_not_called()

    def test_stale_timestamp_rejected(self, config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(config))
        event = make_event(timestamp=now_ms(-7200))
        assert post_jwt(client, sign(event)).status_code == 401
        builder.build.assert_not_called()

    def test_future_timestamp_rejected(self, config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(config))
        event = make_event(timestamp=now_ms(7200))
        assert post_jwt(client, sign(event)).status_code == 401
        builder.build.assert_not_called()

    def test_missing_timestamp_rejected(self, config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(config))
        assert post_jwt(client, sign(make_event(timestamp=None))).status_code == 401
        builder.build.assert_not_called()


class TestUnsigned:
    def test_plain_json_accepted(self, unsigned_config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(unsigned_config))
        response = post_json(client, make_event())
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        builder.build.assert_called_once()

    def test_jwt_without_secret_rejected(self, unsigned_config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(unsigned_config))
        assert post_jwt(client, sign(make_event())).status_code == 400
        builder.build.assert_not_called()

    def test_invalid_json_rejected(self, unsigned_config, mocks):
        client = TestClient(create_app(unsigned_config))
        assert client.post("/webhook/call-event", content="not json").status_code == 400

    def test_non_dict_payload_rejected(self, unsigned_config, mocks):
        client = TestClient(create_app(unsigned_config))
        assert client.post("/webhook/call-event", content="[1, 2]").status_code == 400


class TestRouting:
    def test_non_trigger_state_ignored(self, config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(config))
        response = post_jwt(client, sign(make_event(state="hangup")))
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        builder.build.assert_not_called()

    def test_trigger_state_call_transcription(self, config, mocks, monkeypatch):
        monkeypatch.setenv("DIALPAD_TRIGGER_STATE", "call_transcription")
        builder, _ = mocks
        client = TestClient(create_app(DialpadConfig()))
        # `recording` is now ignored, `call_transcription` builds.
        assert post_jwt(client, sign(make_event(state="recording"))).json() == {"status": "ignored"}
        response = post_jwt(client, sign(make_event(state="call_transcription")))
        assert response.json()["status"] == "ok"
        builder.build.assert_called_once()

    def test_voicemail_has_own_dedupe_key(self, config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(config))
        assert post_jwt(client, sign(make_event(state="recording"))).json()["status"] == "ok"
        response = post_jwt(client, sign(make_event(state="voicemail_uploaded")))
        assert response.json()["status"] == "ok"
        assert builder.build.call_count == 2
        assert client.get("/status/1234").status_code == 200
        assert client.get("/status/1234:voicemail").status_code == 200

    def test_duplicate_call_skipped(self, config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(config))
        post_jwt(client, sign(make_event()))
        response = post_jwt(client, sign(make_event()))
        assert response.json() == {"status": "already_processed"}
        builder.build.assert_called_once()

    def test_missing_call_id_ignored(self, config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(config))
        event = make_event()
        del event["call_id"]
        response = post_jwt(client, sign(event))
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        builder.build.assert_not_called()


class TestOutcomes:
    def test_nothing_to_build_acks_without_marking(self, config, mocks):
        builder, poster = mocks
        builder.build.return_value = None
        client = TestClient(create_app(config))
        response = post_jwt(client, sign(make_event()))
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        poster.post.assert_not_called()
        assert client.get("/status/1234").status_code == 404

    def test_build_failure_returns_503_and_retries(self, config, mocks):
        builder, poster = mocks
        vcon = builder.build.return_value
        builder.build.side_effect = [DialpadBuildError("download failed"), vcon]
        client = TestClient(create_app(config))
        response = post_jwt(client, sign(make_event()))
        assert response.status_code == 503
        assert client.get("/status/1234").json()["status"] == "build_failed"
        # Dialpad redelivers: the call is not blocked by the failure.
        response = post_jwt(client, sign(make_event()))
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "uuid": "vcon-uuid-1"}
        assert client.get("/status/1234").json()["status"] == "success"
        assert builder.build.call_args_list[0].kwargs == {"allow_partial": False}

    def test_retries_bounded_then_acks(self, config, mocks):
        builder, _ = mocks
        builder.build.side_effect = DialpadBuildError("down")
        client = TestClient(create_app(config))
        codes = [post_jwt(client, sign(make_event())).status_code for _ in range(MAX_ATTEMPTS)]
        assert codes == [503] * (MAX_ATTEMPTS - 1) + [200]
        assert builder.build.call_args_list[-1].kwargs == {"allow_partial": True}

    def test_post_failure_returns_503_and_retries(self, config, mocks):
        _, poster = mocks
        poster.post.side_effect = [False, True]
        client = TestClient(create_app(config))
        response = post_jwt(client, sign(make_event()))
        assert response.status_code == 503
        status = client.get("/status/1234").json()
        assert status["status"] == "post_failed"
        assert status["vcon_uuid"] == "vcon-uuid-1"
        assert post_jwt(client, sign(make_event())).json()["status"] == "ok"

    def test_transcription_event_retries_failed_recording_build(self, config, mocks):
        builder, _ = mocks
        vcon = builder.build.return_value
        builder.build.side_effect = [DialpadBuildError("download failed"), vcon]
        client = TestClient(create_app(config))
        assert post_jwt(client, sign(make_event(state="recording"))).status_code == 503
        response = post_jwt(client, sign(make_event(state="call_transcription")))
        assert response.json() == {"status": "ok", "uuid": "vcon-uuid-1"}
        assert builder.build.call_args.args[0]["state"] == "call_transcription"

    def test_transcription_event_ignored_after_success(self, config, mocks):
        builder, _ = mocks
        client = TestClient(create_app(config))
        post_jwt(client, sign(make_event(state="recording")))
        response = post_jwt(client, sign(make_event(state="call_transcription")))
        assert response.json() == {"status": "ignored"}
        builder.build.assert_called_once()


class TestEndpoints:
    def test_health(self, config, mocks):
        response = TestClient(create_app(config)).get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == "vcon-dialpad-adapter"

    def test_status_unknown_404(self, config, mocks):
        assert TestClient(create_app(config)).get("/status/nope").status_code == 404

    def test_status_found(self, config, mocks):
        client = TestClient(create_app(config))
        post_jwt(client, sign(make_event()))
        body = client.get("/status/1234").json()
        assert body == {"call_id": "1234", "vcon_uuid": "vcon-uuid-1", "status": "success"}
