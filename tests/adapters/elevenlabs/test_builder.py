"""Tests for the ElevenLabs vCon builder."""

import base64
import json

from adapters.elevenlabs.builder import ElevenLabsVconBuilder
from core.lawful_basis import LawfulBasisConfig


def make_transcription_event(**data_overrides):
    data = {
        "agent_id": "agent-1",
        "conversation_id": "conv-abc",
        "status": "done",
        "user_id": "user-1",
        "transcript": [
            {"role": "agent", "message": "Hi, how can I help?", "time_in_call_secs": 0},
            {"role": "user", "message": "I need support", "time_in_call_secs": 2},
        ],
        "metadata": {
            "start_time_unix_secs": 1739537297,
            "call_duration_secs": 22,
            "cost": 296,
        },
        "analysis": {"call_successful": "success", "transcript_summary": "Support request."},
    }
    data.update(data_overrides)
    return {"type": "post_call_transcription", "event_timestamp": 1739537297, "data": data}


def make_audio_event(**data_overrides):
    data = {
        "agent_id": "agent-1",
        "conversation_id": "conv-abc",
        "full_audio": base64.b64encode(b"fake-mp3-bytes").decode("ascii"),
    }
    data.update(data_overrides)
    return {"type": "post_call_audio", "event_timestamp": 1739537297, "data": data}


class TestElevenLabsBuilderTranscription:
    def test_ignores_unknown_event_type(self):
        builder = ElevenLabsVconBuilder()
        assert builder.build({"type": "call_initiation_failure", "data": {}}) is None

    def test_builds_text_dialogs(self):
        builder = ElevenLabsVconBuilder()
        vcon = builder.build(make_transcription_event())

        text_dialogs = [d for d in vcon.vcon_dict["dialog"] if d.get("type") == "text"]
        assert len(text_dialogs) == 2
        assert text_dialogs[0]["originator"] == 1  # agent
        assert text_dialogs[1]["originator"] == 0  # user

    def test_analysis_entries(self):
        builder = ElevenLabsVconBuilder()
        vcon = builder.build(make_transcription_event())
        analysis_types = {a["type"] for a in vcon.vcon_dict.get("analysis", [])}
        assert "summary" in analysis_types
        assert "success_evaluation" in analysis_types

    def test_call_record_attachment(self):
        builder = ElevenLabsVconBuilder()
        vcon = builder.build(make_transcription_event())
        records = [
            a for a in vcon.vcon_dict.get("attachments", []) if a.get("purpose") == "call_record"
        ]
        assert len(records) == 1
        body = json.loads(records[0]["body"])
        assert body["durationSeconds"] == 22

    def test_tags_include_conversation_and_agent_id(self):
        builder = ElevenLabsVconBuilder()
        vcon = builder.build(make_transcription_event())
        tags_attachment = next(a for a in vcon.vcon_dict["attachments"] if a["purpose"] == "tags")
        assert "conversation_id:conv-abc" in tags_attachment["body"]
        assert "agent_id:agent-1" in tags_attachment["body"]

    def test_no_lawful_basis_by_default(self):
        builder = ElevenLabsVconBuilder()
        vcon = builder.build(make_transcription_event())
        lawful = [
            a for a in vcon.vcon_dict.get("attachments", []) if a.get("purpose") == "lawful_basis"
        ]
        assert lawful == []

    def test_lawful_basis_applied_when_configured(self):
        builder = ElevenLabsVconBuilder(lawful_basis=LawfulBasisConfig(lawful_basis="consent"))
        vcon = builder.build(make_transcription_event())
        lawful = [
            a for a in vcon.vcon_dict.get("attachments", []) if a.get("purpose") == "lawful_basis"
        ]
        assert len(lawful) == 1


class TestElevenLabsBuilderAudio:
    def test_builds_recording_dialog_embedded(self):
        builder = ElevenLabsVconBuilder()
        vcon = builder.build(make_audio_event())
        recordings = [d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording"]
        assert len(recordings) == 1
        assert recordings[0]["encoding"] == "base64url"
        assert recordings[0]["mediatype"] == "audio/mpeg"
        assert "content_hash" in recordings[0]

    def test_missing_audio_returns_none(self):
        builder = ElevenLabsVconBuilder()
        event = make_audio_event()
        del event["data"]["full_audio"]
        assert builder.build(event) is None

    def test_bad_base64_returns_none(self):
        builder = ElevenLabsVconBuilder()
        event = make_audio_event(full_audio="not-valid-base64!!!")
        assert builder.build(event) is None
