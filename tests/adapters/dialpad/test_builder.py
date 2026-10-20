"""Tests for the Dialpad vCon builder.

Fixtures are constructed from Dialpad's documented call event and API payload
shapes, not captured traffic. The Dialpad client is replaced with a mock.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import jsonschema
import pytest

from adapters.dialpad.builder import DialpadBuildError, DialpadVconBuilder
from tests.adapters.dialpad.conftest import load_fixture

SCHEMA = json.loads((Path(__file__).parents[2] / "schema" / "vcon_json_schema.json").read_text())

AUDIO = b"ID3 not real mp3, just deterministic bytes"


def make_client(call=None, transcript=None, recap=None, audio=AUDIO):
    client = MagicMock()
    client.get_call.return_value = call
    client.get_transcript.return_value = transcript
    client.get_ai_recap.return_value = recap
    client.download_recording.return_value = audio
    return client


def analysis_by_type(vcon):
    return {a["type"]: a for a in vcon.vcon_dict.get("analysis", [])}


def tags(vcon):
    attachment = next(a for a in vcon.vcon_dict["attachments"] if a["purpose"] == "tags")
    return dict(t.split(":", 1) for t in attachment["body"])


class TestRecordingEvent:
    def test_basic_build(self):
        builder = DialpadVconBuilder(make_client())
        vcon = builder.build(load_fixture("recording_event.json"))

        assert vcon is not None
        parties = vcon.vcon_dict["parties"]
        assert parties[0]["tel"] == "+15555550100"
        assert parties[0]["name"] == "Test Caller"
        assert parties[0]["mailto"] == "caller@example.com"
        assert parties[1]["tel"] == "+15555550199"
        assert parties[1]["name"] == "Test Agent"

        dialogs = vcon.vcon_dict["dialog"]
        assert len(dialogs) == 1
        dialog = dialogs[0]
        assert dialog["type"] == "recording"
        assert dialog["mediatype"] == "audio/mpeg"
        assert dialog["duration"] == 58.0
        assert dialog["start"].startswith("2025-10-09")
        assert dialog["encoding"] == "base64url"
        assert dialog["parties"] == [0, 1]
        assert dialog["originator"] == 0
        assert "recording_set" not in dialog

    def test_schema_valid(self):
        vcon = DialpadVconBuilder(make_client()).build(load_fixture("recording_event.json"))
        jsonschema.validate(instance=vcon.vcon_dict, schema=SCHEMA)

    def test_outbound_originator_is_target(self):
        event = load_fixture("recording_event.json")
        event["direction"] = "outbound"
        vcon = DialpadVconBuilder(make_client()).build(event)
        assert vcon.vcon_dict["dialog"][0]["originator"] == 1

    def test_party_falls_back_to_numbers(self):
        event = load_fixture("recording_event.json")
        del event["contact"]
        del event["target"]
        vcon = DialpadVconBuilder(make_client()).build(event)
        assert vcon.vcon_dict["parties"][0]["tel"] == "+15555550100"
        assert vcon.vcon_dict["parties"][1]["tel"] == "+15555550199"

    def test_start_falls_back_to_date_connected(self):
        event = load_fixture("recording_event.json")
        del event["recording_details"][0]["start_time"]
        vcon = DialpadVconBuilder(make_client()).build(event)
        assert vcon.vcon_dict["dialog"][0]["start"].startswith("2025-10-09")

    def test_start_time_as_iso_string(self):
        event = load_fixture("recording_event.json")
        event["recording_details"][0]["start_time"] = "2025-10-09T08:53:26Z"
        vcon = DialpadVconBuilder(make_client()).build(event)
        assert vcon.vcon_dict["dialog"][0]["start"].startswith("2025-10-09T08:53:26")

    def test_tags(self):
        vcon = DialpadVconBuilder(make_client()).build(load_fixture("recording_event.json"))
        t = tags(vcon)
        assert t["source"] == "dialpad_adapter"
        assert t["call_id"] == "5550001"
        assert t["master_call_id"] == "5550001"
        assert t["entry_point_call_id"] == "5550001"
        assert t["operator_call_id"] == "5550002"
        assert t["direction"] == "inbound"
        assert t["group_id"] == "9001"
        assert t["state"] == "recording"

    def test_unsupported_state_returns_none(self):
        event = load_fixture("recording_event.json")
        event["state"] = "hangup"
        assert DialpadVconBuilder(make_client()).build(event) is None

    def test_no_recording_returns_none(self):
        event = load_fixture("recording_event.json")
        event["recording_details"] = []
        client = make_client(call={"recording_details": []})
        assert DialpadVconBuilder(client).build(event) is None

    def test_download_failure_raises_retryable(self):
        client = make_client(audio=None)
        with pytest.raises(DialpadBuildError):
            DialpadVconBuilder(client).build(load_fixture("recording_event.json"))

    def test_get_call_failure_raises_retryable(self):
        client = make_client(call=None)
        with pytest.raises(DialpadBuildError):
            DialpadVconBuilder(client).build(load_fixture("transcription_event.json"))

    def test_unexpected_error_raises_retryable(self):
        client = make_client()
        client.download_recording.side_effect = RuntimeError("boom")
        with pytest.raises(DialpadBuildError):
            DialpadVconBuilder(client).build(load_fixture("recording_event.json"))

    def test_hostile_recording_id_cannot_escape_temp_dir(self, tmp_path):
        from core.media_publisher import FilesystemPublisher

        event = load_fixture("recording_event.json")
        event["recording_details"][0]["id"] = "../../escape"
        publisher = FilesystemPublisher(destination=str(tmp_path / "media"))
        vcon = DialpadVconBuilder(make_client(), publisher=publisher).build(event)
        assert vcon.vcon_dict["dialog"][0]["filename"] == "______escape.mp3"
        assert (tmp_path / "media" / "______escape.mp3").exists()

    def test_malformed_transcript_and_recap_keep_recording(self):
        client = make_client(transcript={"lines": ["not-a-dict"]}, recap={"summary": "flat"})
        vcon = DialpadVconBuilder(client).build(load_fixture("recording_event.json"))
        assert len(vcon.vcon_dict["dialog"]) == 1

    def test_admin_recording_skipped_by_default(self):
        event = load_fixture("recording_event.json")
        event["recording_details"] = load_fixture("call.json")["recording_details"][2:]
        client = make_client()
        assert DialpadVconBuilder(client).build(event) is None
        client.download_recording.assert_not_called()

    def test_admin_recording_included_when_enabled(self):
        event = load_fixture("recording_event.json")
        event["recording_details"] = load_fixture("call.json")["recording_details"][2:]
        vcon = DialpadVconBuilder(make_client(), include_admin_recordings=True).build(event)
        assert len(vcon.vcon_dict["dialog"]) == 1

    def test_download_disabled_references_url(self):
        client = make_client()
        builder = DialpadVconBuilder(client, download_recordings=False)
        vcon = builder.build(load_fixture("recording_event.json"))
        dialog = vcon.vcon_dict["dialog"][0]
        assert dialog["url"] == "https://dialpad.com/r/rec-1"
        assert "body" not in dialog
        client.download_recording.assert_not_called()


class TestMultipleRecordings:
    def make_event(self):
        event = load_fixture("recording_event.json")
        event["recording_details"] = load_fixture("call.json")["recording_details"][:2]
        return event

    def test_recording_set_added(self):
        vcon = DialpadVconBuilder(make_client()).build(self.make_event())
        dialogs = vcon.vcon_dict["dialog"]
        assert [d["type"] for d in dialogs] == ["recording", "recording", "recording-set"]
        assert dialogs[2]["recordings"] == [0, 1]
        assert dialogs[0]["recording_set"] == 2
        assert dialogs[1]["recording_set"] == 2
        # first starts at +6s for 58s, second at +40s for 20s: spans 58s
        assert dialogs[2]["duration"] == pytest.approx(58.0)
        assert dialogs[2]["start"] == dialogs[0]["start"]

    def test_schema_valid(self):
        vcon = DialpadVconBuilder(make_client()).build(self.make_event())
        jsonschema.validate(instance=vcon.vcon_dict, schema=SCHEMA)

    def test_partial_failure_raises_retryable(self):
        client = make_client()
        client.download_recording.side_effect = [AUDIO, None]
        with pytest.raises(DialpadBuildError):
            DialpadVconBuilder(client).build(self.make_event())

    def test_partial_allowed_keeps_recordings_without_orphan_set(self):
        client = make_client()
        client.download_recording.side_effect = [AUDIO, None]
        vcon = DialpadVconBuilder(client).build(self.make_event(), allow_partial=True)
        dialogs = vcon.vcon_dict["dialog"]
        assert [d["type"] for d in dialogs] == ["recording"]


class TestCallTranscriptionEvent:
    def test_fetches_recordings_via_get_call(self):
        client = make_client(
            call=load_fixture("call.json"), transcript=load_fixture("transcript.json")
        )
        vcon = DialpadVconBuilder(client).build(load_fixture("transcription_event.json"))
        client.get_call.assert_called_once_with(5550001)
        # two callrecordings; admin one skipped, so a recording-set groups them
        types = [d["type"] for d in vcon.vcon_dict["dialog"]]
        assert types == ["recording", "recording", "recording-set"]
        assert vcon.vcon_dict["dialog"][0]["originator"] == 1  # outbound

    def test_no_get_call_when_event_has_recordings(self):
        client = make_client()
        DialpadVconBuilder(client).build(load_fixture("recording_event.json"))
        client.get_call.assert_not_called()


class TestTranscript:
    def test_transcript_and_moments(self):
        client = make_client(transcript=load_fixture("transcript.json"))
        vcon = DialpadVconBuilder(client).build(load_fixture("recording_event.json"))
        analyses = analysis_by_type(vcon)

        transcript = analyses["transcript"]
        assert transcript["vendor"] == "dialpad"
        assert transcript["product"] == "dialpad-ai-transcript"
        assert transcript["encoding"] == "json"
        lines = json.loads(transcript["body"])
        assert [ln["type"] for ln in lines] == ["transcript", "transcript"]

        moments = json.loads(analyses["moments"]["body"])
        assert [ln["content"] for ln in moments] == ["Order status discussed"]
        client.get_transcript.assert_called_once_with(5550001)

    def test_missing_transcript_is_silent(self):
        vcon = DialpadVconBuilder(make_client(transcript=None)).build(
            load_fixture("recording_event.json")
        )
        assert "transcript" not in analysis_by_type(vcon)

    def test_empty_transcript_is_silent(self):
        vcon = DialpadVconBuilder(make_client(transcript={"call_id": 1, "lines": []})).build(
            load_fixture("recording_event.json")
        )
        assert "transcript" not in analysis_by_type(vcon)
        assert "moments" not in analysis_by_type(vcon)

    def test_transcript_disabled(self):
        client = make_client(transcript=load_fixture("transcript.json"))
        vcon = DialpadVconBuilder(client, include_transcript=False).build(
            load_fixture("recording_event.json")
        )
        assert "transcript" not in analysis_by_type(vcon)
        client.get_transcript.assert_not_called()

    def test_schema_valid(self):
        client = make_client(
            call=load_fixture("call.json"), transcript=load_fixture("transcript.json")
        )
        vcon = DialpadVconBuilder(client).build(load_fixture("transcription_event.json"))
        jsonschema.validate(instance=vcon.vcon_dict, schema=SCHEMA)


class TestRecap:
    def test_recap_from_event(self):
        client = make_client(call=load_fixture("call.json"))
        vcon = DialpadVconBuilder(client).build(load_fixture("transcription_event.json"))
        analyses = analysis_by_type(vcon)
        assert analyses["summary"]["body"].startswith("The caller asked")
        assert analyses["summary"]["product"] == "dialpad-ai-recap"
        assert analyses["outcome"]["body"] == "Resolved"
        assert json.loads(analyses["purposes"]["body"]) == ["Order status"]
        assert json.loads(analyses["action_items"]["body"]) == ["Send tracking link"]
        client.get_ai_recap.assert_not_called()

    def test_recap_from_endpoint_when_event_has_none(self):
        client = make_client(recap=load_fixture("ai_recap.json"))
        vcon = DialpadVconBuilder(client).build(load_fixture("recording_event.json"))
        analyses = analysis_by_type(vcon)
        assert analyses["summary"]["body"] == "Short order status call."
        assert analyses["outcome"]["body"] == "Resolved"
        assert json.loads(analyses["purposes"]["body"]) == ["Order status"]
        assert json.loads(analyses["action_items"]["body"]) == ["Send tracking link"]
        client.get_ai_recap.assert_called_once_with(5550001)

    def test_recap_unavailable_is_silent(self):
        vcon = DialpadVconBuilder(make_client(recap=None)).build(
            load_fixture("recording_event.json")
        )
        assert "summary" not in analysis_by_type(vcon)

    def test_recap_disabled(self):
        client = make_client(call=load_fixture("call.json"), recap=load_fixture("ai_recap.json"))
        vcon = DialpadVconBuilder(client, include_recap=False).build(
            load_fixture("transcription_event.json")
        )
        assert "summary" not in analysis_by_type(vcon)
        client.get_ai_recap.assert_not_called()


class TestVoicemail:
    def test_voicemail_build(self):
        client = make_client()
        vcon = DialpadVconBuilder(client).build(load_fixture("voicemail_event.json"))
        assert vcon is not None
        dialog = vcon.vcon_dict["dialog"][0]
        assert dialog["duration"] == 9.0
        transcript = analysis_by_type(vcon)["transcript"]
        assert transcript["product"] == "dialpad-voicemail-transcription"
        assert transcript["vendor"] == "dialpad"
        assert transcript["body"] == "Hello, please call me back."
        assert tags(vcon)["state"] == "voicemail_uploaded"
        client.get_transcript.assert_not_called()
        client.get_ai_recap.assert_not_called()
        jsonschema.validate(instance=vcon.vcon_dict, schema=SCHEMA)

    def test_voicemail_link_fallback(self):
        event = load_fixture("voicemail_event.json")
        del event["recording_details"]
        client = make_client()
        vcon = DialpadVconBuilder(client).build(event)
        assert vcon is not None
        detail = client.download_recording.call_args.args[0]
        assert detail == {"url": "https://dialpad.com/v/vm-1", "recording_type": "voicemail"}
