"""Tests for the Dialpad webhook endpoint: JWT validation and the call lifecycle.

The real builder runs against a fake Dialpad client and a fake poster, so no
network is touched. Fixtures are made up, not captured traffic.
"""

import copy
import json
import time
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

from adapters.dialpad.client import DialpadAPIError
from adapters.dialpad.config import DialpadConfig
from adapters.dialpad.webhook import MAX_ATTEMPTS, create_app
from tests.adapters.dialpad.conftest import load_fixture

SECRET = "topsecret"


def now_ms(offset_seconds: float = 0) -> int:
    return int((time.time() + offset_seconds) * 1000)


def rec(rec_id, start_s=6, duration_s=30, recording_type="callrecording"):
    return {
        "id": rec_id,
        "url": f"https://dialpad.com/r/{rec_id}",
        "duration": duration_s * 1000,
        "start_time": 1760000000000 + start_s * 1000,
        "recording_type": recording_type,
    }


def make_event(state="recording", call_id=1234, timestamp="default", recordings=None, **extra):
    event = {
        "state": state,
        "call_id": call_id,
        "direction": "inbound",
        "external_number": "+15555550100",
        "internal_number": "+15555550199",
        **extra,
    }
    if recordings is None and state == "recording":
        recordings = [rec("rec-1")]
    if recordings is not None:
        event["recording_details"] = recordings
    if timestamp == "default":
        event["event_timestamp"] = now_ms()
    elif timestamp is not None:
        event["event_timestamp"] = timestamp
    return event


def sign(event, secret=SECRET, algorithm="HS256"):
    return jwt.encode(event, secret, algorithm=algorithm)


def base_env(monkeypatch, tmp_path):
    for name in ("LAWFUL_BASIS", "DIALPAD_UPDATE_ON_LATER_EVENTS", "MEDIA_BACKEND"):
        monkeypatch.delenv(name, raising=False)
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


class FakeDialpad:
    """Stands in for DialpadClient. Mutate the attributes to move the call on."""

    def __init__(self):
        self.call = {"call_id": 1234, "recording_details": [rec("rec-1")]}
        self.transcript = None
        self.recap = None
        self.failing = set()  # recording ids whose download fails
        self.download_delay = 0.0
        self.transient = set()  # API reads that fail transiently, by method name
        self.calls = {"get_call": 0, "get_transcript": 0, "get_ai_recap": 0, "download": 0}

    def get_call(self, call_id):
        self.calls["get_call"] += 1
        self._maybe_fail("get_call")
        return copy.deepcopy(self.call)

    def get_transcript(self, call_id):
        self.calls["get_transcript"] += 1
        self._maybe_fail("get_transcript")
        return copy.deepcopy(self.transcript)

    def get_ai_recap(self, call_id):
        self.calls["get_ai_recap"] += 1
        self._maybe_fail("get_ai_recap")
        return copy.deepcopy(self.recap)

    def _maybe_fail(self, name):
        if name in self.transient:
            raise DialpadAPIError(f"{name} unavailable")

    def download_recording(self, detail):
        self.calls["download"] += 1
        if self.download_delay:
            time.sleep(self.download_delay)
        if detail.get("id") in self.failing:
            return None
        return f"audio for {detail.get('id')}".encode()


class FakePoster:
    def __init__(self):
        self.posted = []  # vcon dicts, in post order
        self.results = []  # queued return values; True once empty
        self.crash_after_post = False

    def post(self, vcon):
        ok = self.results.pop(0) if self.results else True
        if ok:
            self.posted.append(copy.deepcopy(vcon.vcon_dict))
        if self.crash_after_post:
            raise CrashError("process died after the post landed")
        return ok


class CrashError(Exception):
    pass


@pytest.fixture
def fakes():
    """Patch the Dialpad client and the poster; yield (dialpad, poster)."""
    dialpad = FakeDialpad()
    poster = FakePoster()
    with (
        patch.object(DialpadConfig, "build_client", lambda self: dialpad),
        patch("adapters.dialpad.webhook.HttpPoster", lambda *a, **k: poster),
    ):
        yield dialpad, poster


def analysis_types(vcon_dict):
    return [a["type"] for a in vcon_dict.get("analysis", [])]


def tag_map(vcon_dict):
    tags = next(a for a in vcon_dict["attachments"] if a.get("purpose") == "tags")
    return dict(t.split(":", 1) for t in tags["body"])


def post_jwt(client, token):
    return client.post("/webhook/call-event", content=token)


def post_json(client, event):
    return client.post("/webhook/call-event", content=json.dumps(event))


def send(client, state="recording", **kwargs):
    return post_jwt(client, sign(make_event(state=state, **kwargs)))


def status(client, call_id=1234):
    return client.get(f"/status/{call_id}").json()


class TestSignature:
    def test_valid_jwt_builds(self, config, fakes):
        _, poster = fakes
        client = TestClient(create_app(config))
        response = post_jwt(client, sign(make_event()))
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "uuid": poster.posted[0]["uuid"]}
        assert len(poster.posted) == 1

    @pytest.mark.parametrize(
        "token",
        [
            sign(make_event(), secret="other"),
            jwt.encode(make_event(), None, algorithm="none"),
            sign(make_event(), algorithm="HS512"),
            sign(make_event(timestamp=now_ms(-7200))),
            sign(make_event(timestamp=now_ms(7200))),
            sign(make_event(timestamp=None)),
        ],
        ids=["wrong-secret", "alg-none", "hs512", "stale", "future", "no-timestamp"],
    )
    def test_rejected(self, config, fakes, token):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        assert post_jwt(client, token).status_code == 401
        assert poster.posted == []
        assert dialpad.calls["download"] == 0

    def test_plain_json_rejected_when_secret_configured(self, config, fakes):
        _, poster = fakes
        client = TestClient(create_app(config))
        assert post_json(client, make_event()).status_code == 401
        assert poster.posted == []


class TestUnsigned:
    def test_plain_json_accepted(self, unsigned_config, fakes):
        _, poster = fakes
        client = TestClient(create_app(unsigned_config))
        response = post_json(client, make_event())
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert len(poster.posted) == 1

    def test_jwt_without_secret_rejected(self, unsigned_config, fakes):
        _, poster = fakes
        client = TestClient(create_app(unsigned_config))
        assert post_jwt(client, sign(make_event())).status_code == 400
        assert poster.posted == []

    def test_invalid_json_rejected(self, unsigned_config, fakes):
        client = TestClient(create_app(unsigned_config))
        assert client.post("/webhook/call-event", content="not json").status_code == 400

    def test_non_dict_payload_rejected(self, unsigned_config, fakes):
        client = TestClient(create_app(unsigned_config))
        assert client.post("/webhook/call-event", content="[1, 2]").status_code == 400


class TestRouting:
    def test_unhandled_state_ignored(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        response = send(client, "hangup")
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        assert poster.posted == []
        assert dialpad.calls["get_call"] == 0

    def test_missing_call_id_ignored(self, config, fakes):
        _, poster = fakes
        client = TestClient(create_app(config))
        event = make_event()
        del event["call_id"]
        response = post_jwt(client, sign(event))
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        assert poster.posted == []

    def test_nothing_to_build_acks_without_marking(self, config, fakes):
        dialpad, poster = fakes
        dialpad.call = {"call_id": 1234, "recording_details": []}
        client = TestClient(create_app(config))
        response = send(client, "call_transcription")
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        assert poster.posted == []
        assert client.get("/status/1234").status_code == 404


class TestLifecycle:
    def test_transcript_after_recording_updates_same_uuid(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        first = send(client, "recording").json()
        assert first["status"] == "ok"
        assert "transcript" not in analysis_types(poster.posted[0])

        dialpad.transcript = load_fixture("transcript.json")
        second = send(client, "call_transcription").json()
        assert second == {"status": "updated", "uuid": first["uuid"]}
        assert len(poster.posted) == 2
        assert poster.posted[1]["uuid"] == first["uuid"]
        assert poster.posted[1]["created_at"] == poster.posted[0]["created_at"]
        assert "transcript" in analysis_types(poster.posted[1])
        assert tag_map(poster.posted[1])["revision"] == "2"
        assert status(client) == {
            "call_id": "1234",
            "vcon_uuid": first["uuid"],
            "status": "posted",
            "revision": 2,
        }

    def test_recap_components_accumulate_on_same_uuid(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        uuid = send(client, "recording").json()["uuid"]
        dialpad.transcript = load_fixture("transcript.json")
        send(client, "call_transcription")

        response = send(client, "recap_summary", recap_summary="A short order call.")
        assert response.json() == {"status": "updated", "uuid": uuid}
        types = analysis_types(poster.posted[-1])
        assert "transcript" in types and "summary" in types

        send(client, "recap_outcome", recap_outcome="Resolved")
        types = analysis_types(poster.posted[-1])
        assert {"transcript", "summary", "outcome"} <= set(types)
        assert {p["uuid"] for p in poster.posted} == {uuid}

        # A component already posted adds nothing.
        repeat = send(client, "recap_summary", recap_summary="A short order call.")
        assert repeat.json() == {"status": "already_processed"}
        assert len(poster.posted) == 4

    def test_call_without_ai_built_from_recording_alone(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        assert send(client, "recording").json()["status"] == "ok"
        posted = poster.posted[0]
        assert [d["type"] for d in posted["dialog"]] == ["recording"]
        assert "transcript" not in analysis_types(posted)
        assert status(client)["status"] == "posted"

    def test_second_recording_event_adds_recording_set(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        uuid = send(client, "recording", recordings=[rec("rec-1")]).json()["uuid"]

        dialpad.call["recording_details"] = [rec("rec-1"), rec("rec-2", start_s=40)]
        response = send(client, "recording", recordings=[rec("rec-2", start_s=40)])
        assert response.json() == {"status": "updated", "uuid": uuid}
        dialogs = poster.posted[-1]["dialog"]
        assert [d["type"] for d in dialogs] == ["recording", "recording", "recording-set"]
        assert [d["filename"] for d in dialogs[:2]] == ["rec-1.mp3", "rec-2.mp3"]
        assert dialogs[2]["recordings"] == [0, 1]

    def test_duplicate_events_do_not_repost(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        send(client, "recording")
        assert send(client, "recording").json() == {"status": "already_processed"}
        dialpad.transcript = load_fixture("transcript.json")
        send(client, "call_transcription")
        downloads = dialpad.calls["download"]
        assert send(client, "call_transcription").json() == {"status": "already_processed"}
        assert len(poster.posted) == 2
        assert dialpad.calls["download"] == downloads

    def test_transcript_not_served_yet_is_not_reposted(self, config, fakes):
        _, poster = fakes
        client = TestClient(create_app(config))
        send(client, "recording")
        response = send(client, "call_transcription")
        assert response.json() == {"status": "already_processed"}
        assert len(poster.posted) == 1
        assert status(client)["status"] == "posted"

    def test_out_of_order_transcription_first(self, config, fakes):
        dialpad, poster = fakes
        dialpad.transcript = load_fixture("transcript.json")
        client = TestClient(create_app(config))
        first = send(client, "call_transcription").json()
        assert first["status"] == "ok"
        assert "transcript" in analysis_types(poster.posted[0])
        # The recording it already holds adds nothing.
        assert send(client, "recording").json() == {"status": "already_processed"}
        assert len(poster.posted) == 1

    def test_voicemail(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        event = load_fixture("voicemail_event.json")
        event["event_timestamp"] = now_ms()
        response = post_jwt(client, sign(event))
        assert response.json()["status"] == "ok"
        posted = poster.posted[0]
        transcript = next(a for a in posted["analysis"] if a["type"] == "transcript")
        assert transcript["product"] == "dialpad-voicemail-transcription"
        assert dialpad.calls["get_transcript"] == 0
        assert dialpad.calls["get_ai_recap"] == 0
        assert post_jwt(client, sign(event)).json() == {"status": "already_processed"}
        assert status(client, 5550003)["status"] == "posted"

    def test_update_disabled_posts_once(self, config, fakes, monkeypatch):
        monkeypatch.setenv("DIALPAD_UPDATE_ON_LATER_EVENTS", "false")
        dialpad, poster = fakes
        client = TestClient(create_app(DialpadConfig()))
        send(client, "recording")
        dialpad.transcript = load_fixture("transcript.json")
        assert send(client, "call_transcription").json() == {"status": "already_processed"}
        assert len(poster.posted) == 1

    def test_lawful_basis_once_across_rebuilds(self, config, fakes, monkeypatch):
        monkeypatch.setenv("LAWFUL_BASIS", "consent")
        dialpad, poster = fakes
        client = TestClient(create_app(DialpadConfig()))
        send(client, "recording", recordings=[rec("rec-1")])
        dialpad.transcript = load_fixture("transcript.json")
        send(client, "call_transcription")
        send(client, "recap_summary", recap_summary="A short order call.")
        dialpad.call["recording_details"] = [rec("rec-1"), rec("rec-2", start_s=40)]
        send(client, "recording", recordings=[rec("rec-2", start_s=40)])
        assert len(poster.posted) == 4
        for posted in poster.posted:
            basis = [a for a in posted["attachments"] if a.get("purpose") == "lawful_basis"]
            assert len(basis) == 1
            tags = [a for a in posted["attachments"] if a.get("purpose") == "tags"]
            assert len(tags) == 1

    def test_restart_keeps_updating_same_uuid(self, config, fakes):
        dialpad, poster = fakes
        uuid = send(TestClient(create_app(config)), "recording").json()["uuid"]
        # A new process: new app, new StateTracker on the same state file.
        restarted = TestClient(create_app(DialpadConfig()))
        dialpad.transcript = load_fixture("transcript.json")
        assert send(restarted, "call_transcription").json() == {"status": "updated", "uuid": uuid}
        assert status(restarted)["revision"] == 2

    def test_call_locks_released(self, config, fakes):
        app = create_app(config)
        client = TestClient(app)
        send(client, "recording")
        send(client, "recording")
        assert len(app.state.call_locks) == 0


class TestRetries:
    def test_build_failure_returns_503_and_retries(self, config, fakes):
        dialpad, poster = fakes
        dialpad.failing = {"rec-1"}
        client = TestClient(create_app(config))
        assert send(client, "recording").status_code == 503
        assert status(client)["status"] == "pending"
        dialpad.failing = set()
        response = send(client, "recording")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert status(client)["status"] == "posted"

    def test_any_later_event_retries_pending_call(self, config, fakes):
        dialpad, poster = fakes
        dialpad.failing = {"rec-1"}
        client = TestClient(create_app(config))
        assert send(client, "recording").status_code == 503
        dialpad.failing = set()
        assert send(client, "call_transcription").json()["status"] == "ok"
        assert len(poster.posted) == 1

    def test_post_failure_keeps_uuid_on_retry(self, config, fakes):
        _, poster = fakes
        poster.results = [False]
        client = TestClient(create_app(config))
        assert send(client, "recording").status_code == 503
        reserved = status(client)
        assert reserved["status"] == "pending"
        assert reserved["vcon_uuid"]
        response = send(client, "recording").json()
        assert response == {"status": "ok", "uuid": reserved["vcon_uuid"]}

    def test_give_up_is_final(self, config, fakes):
        dialpad, poster = fakes
        dialpad.failing = {"rec-1"}
        client = TestClient(create_app(config))
        codes = [send(client, "recording").status_code for _ in range(MAX_ATTEMPTS)]
        assert codes == [503] * (MAX_ATTEMPTS - 1) + [200]
        assert status(client)["status"] == "abandoned"

        dialpad.failing = set()
        dialpad.transcript = load_fixture("transcript.json")
        assert send(client, "recording").json() == {"status": "already_processed"}
        assert send(client, "call_transcription").json() == {"status": "already_processed"}
        assert poster.posted == []

    def test_give_up_posts_partial_once(self, config, fakes):
        dialpad, poster = fakes
        dialpad.failing = {"rec-2"}
        both = [rec("rec-1"), rec("rec-2", start_s=40)]
        client = TestClient(create_app(config))
        codes = [
            send(client, "recording", recordings=both).status_code for _ in range(MAX_ATTEMPTS)
        ]
        assert codes == [503] * (MAX_ATTEMPTS - 1) + [200]
        assert len(poster.posted) == 1
        assert [d["filename"] for d in poster.posted[0]["dialog"]] == ["rec-1.mp3"]
        assert status(client)["status"] == "abandoned"
        assert send(client, "recording", recordings=both).json() == {"status": "already_processed"}
        assert len(poster.posted) == 1

    def test_failed_update_retried_by_next_event_with_same_uuid(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        uuid = send(client, "recording").json()["uuid"]

        dialpad.transcript = load_fixture("transcript.json")
        poster.results = [False]
        assert send(client, "call_transcription").status_code == 503
        # The posted revision is untouched.
        assert status(client) == {
            "call_id": "1234",
            "vcon_uuid": uuid,
            "status": "posted",
            "revision": 1,
        }

        # A recap event carries the retry, and the transcript with it.
        response = send(client, "recap_summary", recap_summary="A short order call.")
        assert response.json() == {"status": "updated", "uuid": uuid}
        types = analysis_types(poster.posted[-1])
        assert "transcript" in types and "summary" in types

    def test_failed_updates_bounded(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        uuid = send(client, "recording").json()["uuid"]
        dialpad.transcript = load_fixture("transcript.json")
        poster.results = [False] * MAX_ATTEMPTS
        codes = [send(client, "call_transcription").status_code for _ in range(MAX_ATTEMPTS)]
        assert codes == [503] * (MAX_ATTEMPTS - 1) + [200]
        final = status(client)
        assert final["status"] == "abandoned"
        assert final["vcon_uuid"] == uuid
        assert final["revision"] == 1
        assert send(client, "recap_summary", recap_summary="x").json() == {
            "status": "already_processed"
        }

    def test_update_never_drops_posted_recording(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        send(client, "recording")
        dialpad.failing = {"rec-1"}
        dialpad.transcript = load_fixture("transcript.json")
        assert send(client, "call_transcription").status_code == 503
        assert len(poster.posted) == 1


class TestTransientEnrichment:
    def test_transcript_fetch_error_at_transcription_retries(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        uuid = send(client, "recording").json()["uuid"]
        dialpad.transcript = load_fixture("transcript.json")
        dialpad.transient = {"get_transcript"}
        assert send(client, "call_transcription").status_code == 503
        assert status(client) == {
            "call_id": "1234",
            "vcon_uuid": uuid,
            "status": "posted",
            "revision": 1,
        }
        assert len(poster.posted) == 1

        dialpad.transient = set()
        assert send(client, "call_transcription").json() == {"status": "updated", "uuid": uuid}
        assert "transcript" in analysis_types(poster.posted[-1])

    def test_transcript_fetch_error_on_first_build_retries(self, config, fakes):
        dialpad, poster = fakes
        dialpad.transcript = load_fixture("transcript.json")
        dialpad.transient = {"get_transcript"}
        client = TestClient(create_app(config))
        assert send(client, "call_transcription").status_code == 503
        assert poster.posted == []
        dialpad.transient = set()
        assert send(client, "call_transcription").json()["status"] == "ok"
        assert "transcript" in analysis_types(poster.posted[-1])

    def test_transcript_error_elsewhere_does_not_block_recording(self, config, fakes):
        dialpad, poster = fakes
        dialpad.transient = {"get_transcript"}
        client = TestClient(create_app(config))
        assert send(client, "recording").json()["status"] == "ok"
        dialpad.transient = set()
        dialpad.transcript = load_fixture("transcript.json")
        assert send(client, "call_transcription").json()["status"] == "updated"

    def test_persistent_transcript_error_gives_up_once(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        uuid = send(client, "recording").json()["uuid"]
        dialpad.transient = {"get_transcript"}
        codes = [send(client, "call_transcription").status_code for _ in range(MAX_ATTEMPTS)]
        assert codes == [503] * (MAX_ATTEMPTS - 1) + [200]
        final = status(client)
        assert (final["status"], final["vcon_uuid"], final["revision"]) == (
            "abandoned",
            uuid,
            1,
        )
        assert len(poster.posted) == 1
        assert send(client, "call_transcription").json() == {"status": "already_processed"}

    def test_recap_fetch_error_at_recap_event_retries(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        uuid = send(client, "recording").json()["uuid"]
        dialpad.recap = {"summary": {"content": "Order call."}}
        dialpad.transient = {"get_ai_recap"}
        # The event does not carry its own component, so only the API has it.
        assert send(client, "recap_summary").status_code == 503
        dialpad.transient = set()
        assert send(client, "recap_summary").json() == {"status": "updated", "uuid": uuid}
        assert "summary" in analysis_types(poster.posted[-1])

    def test_recap_event_carrying_its_component_ignores_api_error(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        uuid = send(client, "recording").json()["uuid"]
        dialpad.transient = {"get_ai_recap"}
        response = send(client, "recap_summary", recap_summary="Order call.")
        assert response.json() == {"status": "updated", "uuid": uuid}

    def test_call_info_error_on_rebuild_retries(self, config, fakes):
        dialpad, poster = fakes
        client = TestClient(create_app(config))
        uuid = send(client, "recording").json()["uuid"]
        dialpad.transcript = load_fixture("transcript.json")
        dialpad.transient = {"get_call"}
        assert send(client, "call_transcription").status_code == 503
        dialpad.transient = set()
        assert send(client, "call_transcription").json() == {"status": "updated", "uuid": uuid}


class TestCrashSafety:
    def test_uuid_persisted_before_first_post(self, config, fakes):
        _, poster = fakes
        poster.crash_after_post = True
        with pytest.raises(CrashError):
            send(TestClient(create_app(config)), "recording")
        landed = poster.posted[0]["uuid"]

        poster.crash_after_post = False
        restarted = TestClient(create_app(DialpadConfig()))
        assert send(restarted, "recording").json()["uuid"] == landed
        assert {v["uuid"] for v in poster.posted} == {landed}


class TestEndpoints:
    def test_health(self, config, fakes):
        response = TestClient(create_app(config)).get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == "vcon-dialpad-adapter"

    def test_status_unknown_404(self, config, fakes):
        assert TestClient(create_app(config)).get("/status/nope").status_code == 404


class TestValidationOffWithSecret:
    @pytest.fixture
    def off_config(self, config, monkeypatch):
        monkeypatch.setenv("VALIDATE_DIALPAD_SIGNATURE", "false")
        return DialpadConfig()

    def test_valid_jwt_accepted(self, off_config, fakes):
        _, poster = fakes
        client = TestClient(create_app(off_config))
        assert post_jwt(client, sign(make_event())).status_code == 200
        assert len(poster.posted) == 1

    def test_bad_jwt_rejected(self, off_config, fakes):
        _, poster = fakes
        client = TestClient(create_app(off_config))
        assert post_jwt(client, sign(make_event(), secret="wrong")).status_code == 401
        assert poster.posted == []

    def test_plain_json_still_accepted(self, off_config, fakes):
        _, poster = fakes
        client = TestClient(create_app(off_config))
        assert post_json(client, make_event()).status_code == 200
        assert len(poster.posted) == 1


class TestConcurrency:
    async def test_same_call_builds_once(self, config, fakes):
        import asyncio

        import httpx

        dialpad, poster = fakes
        dialpad.download_delay = 0.3
        app = create_app(config)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            token = sign(make_event())
            responses = await asyncio.gather(
                *[client.post("/webhook/call-event", content=token) for _ in range(2)]
            )
        statuses = sorted(r.json()["status"] for r in responses)
        assert statuses == ["already_processed", "ok"]
        assert len(poster.posted) == 1
        assert dialpad.calls["download"] == 1
        assert len(app.state.call_locks) == 0

    async def test_slow_build_does_not_block_health(self, config, fakes):
        import asyncio

        import httpx

        dialpad, _ = fakes
        dialpad.download_delay = 0.5
        transport = httpx.ASGITransport(app=create_app(config))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            slow = asyncio.create_task(
                client.post("/webhook/call-event", content=sign(make_event()))
            )
            await asyncio.sleep(0.1)
            start = time.monotonic()
            health = await client.get("/health")
            elapsed = time.monotonic() - start
            assert health.status_code == 200
            assert elapsed < 0.25
            assert (await slow).status_code == 200
