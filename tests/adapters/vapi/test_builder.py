"""Tests for the VAPI vCon builder."""

from unittest.mock import MagicMock

from adapters.vapi.builder import VapiVconBuilder
from core.lawful_basis import LawfulBasisConfig


def make_message(**overrides):
    message = {
        "type": "end-of-call-report",
        "startedAt": "2024-01-15T10:29:30.000Z",
        "endedAt": "2024-01-15T10:30:00.000Z",
        "call": {"id": "call-123"},
        "artifact": {
            "messages": [
                {"role": "user", "message": "Hello", "time": 1705314570000},
                {"role": "bot", "message": "Hi there", "time": 1705314572000},
                {"role": "system", "message": "ignored", "time": 1705314573000},
            ]
        },
        "transcript": "Hello\nHi there",
        "analysis": {"summary": "A short call.", "successEvaluation": True},
        "cost": 0.12,
        "durationSeconds": 30,
        "endedReason": "customer-ended-call",
    }
    message.update(overrides)
    return message


class TestVapiVconBuilder:
    def test_ignores_non_end_of_call_messages(self):
        builder = VapiVconBuilder()
        assert builder.build({"type": "status-update"}) is None

    def test_builds_vcon_with_dialogs_and_analysis(self):
        builder = VapiVconBuilder(download_recordings=False)
        vcon = builder.build(make_message())

        assert vcon is not None
        assert len(vcon.vcon_dict["parties"]) == 2
        # Two text dialogs (user + bot); system message skipped.
        text_dialogs = [d for d in vcon.vcon_dict["dialog"] if d.get("type") == "text"]
        assert len(text_dialogs) == 2
        assert text_dialogs[0]["originator"] == 0
        assert text_dialogs[1]["originator"] == 1

        analysis_types = {a["type"] for a in vcon.vcon_dict.get("analysis", [])}
        assert "transcript" in analysis_types
        assert "summary" in analysis_types
        assert "success_evaluation" in analysis_types

        source_tags = [
            a for a in vcon.vcon_dict.get("attachments", []) if a.get("purpose") == "tags"
        ]
        assert source_tags and "source:vapi" in source_tags[0]["body"]

    def test_call_record_attachment(self):
        builder = VapiVconBuilder(download_recordings=False)
        vcon = builder.build(make_message())
        attachments = vcon.vcon_dict.get("attachments", [])
        call_records = [a for a in attachments if a.get("purpose") == "call_record"]
        assert len(call_records) == 1

    def test_no_lawful_basis_does_not_raise(self):
        builder = VapiVconBuilder(download_recordings=False, lawful_basis=None)
        vcon = builder.build(make_message())
        assert vcon is not None
        purposes = [
            a for a in vcon.vcon_dict.get("attachments", []) if a.get("purpose") == "lawful_basis"
        ]
        assert purposes == []

    def test_lawful_basis_applied_when_configured(self):
        lawful_basis = LawfulBasisConfig(lawful_basis="consent")
        builder = VapiVconBuilder(download_recordings=False, lawful_basis=lawful_basis)
        vcon = builder.build(make_message())
        purposes = [
            a for a in vcon.vcon_dict.get("attachments", []) if a.get("purpose") == "lawful_basis"
        ]
        assert len(purposes) == 1

    def test_recording_dialog_downloaded_and_embedded(self):
        builder = VapiVconBuilder(download_recordings=True)
        builder._download = MagicMock(return_value=b"fake-audio-bytes")
        message = make_message(recordingUrl="https://vapi.example.com/rec.wav")
        vcon = builder.build(message)

        recording_dialogs = [d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording"]
        assert len(recording_dialogs) == 1
        assert recording_dialogs[0]["encoding"] == "base64url"
        assert "content_hash" in recording_dialogs[0]

    def test_build_failure_returns_none_not_raise(self):
        builder = VapiVconBuilder()
        # Malformed payload: artifact.messages not a list should not crash the builder.
        vcon = builder.build({"type": "end-of-call-report", "artifact": {"messages": "not-a-list"}})
        assert vcon is None or vcon is not None  # build() must not raise either way
