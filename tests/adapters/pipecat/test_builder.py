"""Tests for the Pipecat vCon builder."""

import json
from datetime import UTC, datetime, timedelta

from adapters.pipecat.builder import (
    PipecatConversationState,
    PipecatTurn,
    PipecatVconBuilder,
)
from core.lawful_basis import LawfulBasisConfig


def make_state(**overrides):
    started = datetime.now(UTC)
    state = PipecatConversationState(
        conversation_id="conv-1",
        started_at=started,
        ended_at=started + timedelta(seconds=30),
        turns=[
            PipecatTurn(role="user", text="Hello", start_time=started),
            PipecatTurn(role="assistant", text="Hi!", start_time=started + timedelta(seconds=1)),
        ],
    )
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


class TestPipecatVconBuilder:
    def test_builds_text_dialogs(self):
        builder = PipecatVconBuilder()
        vcon = builder.build(make_state())

        text_dialogs = [d for d in vcon.vcon_dict["dialog"] if d.get("type") == "text"]
        assert len(text_dialogs) == 2
        assert text_dialogs[0]["originator"] == 0
        assert text_dialogs[1]["originator"] == 1

    def test_call_record_attachment_has_conversation_id(self):
        builder = PipecatVconBuilder()
        vcon = builder.build(make_state())
        call_records = [
            a for a in vcon.vcon_dict.get("attachments", []) if a.get("purpose") == "call_record"
        ]
        assert len(call_records) == 1
        assert json.loads(call_records[0]["body"])["conversation_id"] == "conv-1"

    def test_no_lawful_basis_by_default(self):
        builder = PipecatVconBuilder()
        vcon = builder.build(make_state())
        lawful = [
            a for a in vcon.vcon_dict.get("attachments", []) if a.get("purpose") == "lawful_basis"
        ]
        assert lawful == []

    def test_lawful_basis_applied_when_configured(self):
        builder = PipecatVconBuilder(lawful_basis=LawfulBasisConfig(lawful_basis="consent"))
        vcon = builder.build(make_state())
        lawful = [
            a for a in vcon.vcon_dict.get("attachments", []) if a.get("purpose") == "lawful_basis"
        ]
        assert len(lawful) == 1

    def test_audio_embedded_without_publisher(self):
        builder = PipecatVconBuilder()
        vcon = builder.build(make_state(audio_bytes=b"fake-wav-bytes"))
        recordings = [d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording"]
        assert len(recordings) == 1
        assert recordings[0]["encoding"] == "base64url"
        assert "content_hash" in recordings[0]

    def test_no_audio_dialog_when_no_bytes(self):
        builder = PipecatVconBuilder()
        vcon = builder.build(make_state())
        recordings = [d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording"]
        assert recordings == []
