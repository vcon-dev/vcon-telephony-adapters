"""ElevenLabs adapter spec compliance tests."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from adapters.elevenlabs.builder import ElevenLabsVconBuilder
from adapters.elevenlabs.models import (
    AudioRecording,
    Conversation,
    ConversationParticipant,
    Transcript,
)


def _sample(*, with_transcript=True, with_audio_url=True) -> Conversation:
    return Conversation(
        id="conv_123",
        agent_id="agent_456",
        start_time=datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc),
        duration_ms=30_000,
        status="completed",
        participants=[
            ConversationParticipant(id="u1", name="User", role="user"),
            ConversationParticipant(id="a1", name="Agent", role="agent"),
        ],
        audio=AudioRecording(
            url="https://example.com/audio.wav" if with_audio_url else None,
            duration_ms=30_000,
            format="wav",
        ),
        transcript=Transcript(text="Hello, world.", language="en") if with_transcript else None,
        tags=["important"],
    )


def test_vcon_syntax_is_0_4_0():
    v = ElevenLabsVconBuilder().build(_sample())
    assert v.vcon_dict["vcon"] == "0.4.0"


def test_dialog_uses_mediatype_not_mimetype():
    v = ElevenLabsVconBuilder().build(_sample())
    dialog = v.vcon_dict["dialog"][0]
    assert dialog["mediatype"] == "audio/wav"
    assert "mimetype" not in dialog


def test_dialog_strips_empty_metadata_meta():
    v = ElevenLabsVconBuilder().build(_sample())
    dialog = v.vcon_dict["dialog"][0]
    assert "metadata" not in dialog
    assert "meta" not in dialog


def test_inline_audio_uses_base64url_and_content_hash():
    audio = b"fake wav \xfe\xff\x00"
    v = ElevenLabsVconBuilder().build(_sample(), audio_bytes=audio)
    dialog = v.vcon_dict["dialog"][0]
    assert dialog["encoding"] == "base64url"
    assert dialog["body"] == base64.urlsafe_b64encode(audio).rstrip(b"=").decode("ascii")
    assert dialog["content_hash"].startswith("sha512-")


def test_call_record_attachment_uses_purpose_and_indices():
    v = ElevenLabsVconBuilder().build(_sample())
    call_record = [a for a in v.vcon_dict["attachments"] if a.get("purpose") == "call_record"]
    assert len(call_record) == 1
    att = call_record[0]
    assert "type" not in att
    assert att["party"] == 0
    assert att["dialog"] == 0
    assert att["encoding"] == "json"
    body = json.loads(att["body"])
    assert body["conversation_id"] == "conv_123"
    assert body["agent_id"] == "agent_456"


def test_transcript_in_analysis_with_vendor_and_schema():
    v = ElevenLabsVconBuilder().build(_sample(with_transcript=True))
    transcripts = [a for a in v.vcon_dict["analysis"] if a.get("type") == "transcript"]
    assert len(transcripts) == 1
    a = transcripts[0]
    assert a["vendor"] == "elevenlabs"
    assert a["product"] == "elevenlabs-conversational-ai"
    assert "schema" in a
    assert "schema_version" not in a
    assert a["encoding"] == "json"
    body = json.loads(a["body"])
    assert body["text"] == "Hello, world."


def test_tags_attachment_has_party_and_dialog():
    v = ElevenLabsVconBuilder().build(_sample())
    tags_atts = [a for a in v.vcon_dict["attachments"] if a.get("purpose") == "tags"]
    assert tags_atts
    for att in tags_atts:
        assert "type" not in att
        assert att["party"] == 0
        assert att["dialog"] == 0
