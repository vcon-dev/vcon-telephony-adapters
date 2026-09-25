"""TeXML `recordingStatusCallback` (form-encoded) reaches the same builder as Call Control JSON.

Payload fields are the ones a real Telnyx TeXML callback carried on 2026-09-13 (smart trunk
Phase 0, session abde437e). Values are synthetic.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from adapters.telnyx.config import TelnyxConfig
from adapters.telnyx.webhook import create_app

TEXML_FORM = {
    "AccountSid": "acct-1",
    "CallSid": "v3:CALLSID",
    "CallSessionId": "sess-texml-1",
    "CallInitiatedAt": "2026-09-13T02:34:48.101000Z",
    "CallStatus": "completed",
    "ConnectionId": "3047649853975824124",
    "Direction": "inbound",
    "From": "+15085550100",
    "To": "+14015550100",
    "RecordingChannels": "2",
    "RecordingDuration": "23",
    "RecordingEndTime": "2026-09-13T02:35:25.068185Z",
    "RecordingSid": "13a61448-d5cd-4f16-b231-308b4c61c953",
    "RecordingSource": "DialVerb",
    "RecordingStartTime": "2026-09-13T02:35:02.522170Z",
    "RecordingStatus": "completed",
    "RecordingUrl": "https://s3.amazonaws.com/bucket/x.mp3?X-Amz-Expires=600&X-Amz-Signature=abc",
}


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("VALIDATE_TELNYX_WEBHOOK", "false")
    return TelnyxConfig()


def test_texml_callback_is_reshaped_and_built(config):
    with (
        patch("adapters.telnyx.webhook.HttpPoster") as poster_cls,
        patch("adapters.telnyx.webhook.TelnyxVconBuilder") as builder_cls,
    ):
        vcon = MagicMock(uuid="vcon-1")
        builder = MagicMock()
        builder.build.return_value = vcon
        builder_cls.return_value = builder
        poster = MagicMock()
        poster.post.return_value = True
        poster_cls.return_value = poster

        client = TestClient(create_app(config))
        r = client.post("/webhook/texml-recording", data=TEXML_FORM)

        assert r.status_code == 200 and r.text == "OK"
        (data,), _ = builder.build.call_args
        assert data.recording_id == TEXML_FORM["RecordingSid"]
        assert data.call_session_id == "sess-texml-1"
        assert data.from_number == "+15085550100"
        assert data.to_number == "+14015550100"
        assert data.direction == "inbound"
        assert data.recording_url == TEXML_FORM["RecordingUrl"]
        assert data.recording_urls == {"mp3": TEXML_FORM["RecordingUrl"]}
        assert data.duration_seconds == pytest.approx(22.5, abs=0.1)
        assert data.platform_tags["recording_channels"] == "dual"
        assert data.platform_tags["call_initiated_at"] == "2026-09-13T02:34:48.101000Z"
        assert data.platform_tags["recording_started_at"] == TEXML_FORM["RecordingStartTime"]
        assert not any(k.startswith("annotation_") for k in data.platform_tags)
        poster.post.assert_called_once_with(vcon)

        # second delivery of the same RecordingSid is a no-op
        r2 = client.post("/webhook/texml-recording", data=TEXML_FORM)
        assert r2.text == "OK" and poster.post.call_count == 1


def test_texml_annotations_become_tags(config):
    form = {
        **TEXML_FORM,
        "Annotation-notice": "notice-v1.wav",
        "Annotation-notice_sha256": "ab" * 32,
        "Annotation-bad name": "dropped",
        "Annotation-": "dropped",
    }
    with (
        patch("adapters.telnyx.webhook.HttpPoster") as poster_cls,
        patch("adapters.telnyx.webhook.TelnyxVconBuilder") as builder_cls,
    ):
        builder_cls.return_value.build.return_value = MagicMock(uuid="vcon-2")
        poster_cls.return_value.post.return_value = True
        client = TestClient(create_app(config))
        assert client.post("/webhook/texml-recording", data=form).text == "OK"
        (data,), _ = builder_cls.return_value.build.call_args
        annotations = {k: v for k, v in data.platform_tags.items() if k.startswith("annotation_")}
        assert annotations == {
            "annotation_notice": "notice-v1.wav",
            "annotation_notice_sha256": "ab" * 32,
        }


def test_texml_callback_ignores_non_completed(config):
    with patch("adapters.telnyx.webhook.TelnyxVconBuilder") as builder_cls:
        client = TestClient(create_app(config))
        r = client.post(
            "/webhook/texml-recording", data={**TEXML_FORM, "RecordingStatus": "in-progress"}
        )
        assert r.text == "OK"
        builder_cls.return_value.build.assert_not_called()
