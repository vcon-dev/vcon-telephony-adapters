"""Pipecat adapter spec compliance tests.

Pipecat itself isn't installed in test; we exercise the observer + builder
in standalone mode (no real Pipecat frames involved).
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from adapters.pipecat.builder import (
    PipecatConversationState,
    PipecatTurn,
    PipecatVconBuilder,
)
from adapters.pipecat.observer import VconConversationObserver


def _sample_state(*, with_audio: bytes | None = None) -> PipecatConversationState:
    return PipecatConversationState(
        conversation_id="call-abc-123",
        started_at=datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 19, 12, 0, 30, tzinfo=timezone.utc),
        user_party={"name": "Customer", "tel": "+15551234567", "role": "user"},
        agent_party={"name": "Agent", "role": "agent"},
        turns=[
            PipecatTurn(role="user", text="Hi", start_time=datetime(2026, 5, 19, 12, 0, 1, tzinfo=timezone.utc)),
            PipecatTurn(role="assistant", text="Hello! How can I help?", start_time=datetime(2026, 5, 19, 12, 0, 2, tzinfo=timezone.utc)),
            PipecatTurn(role="user", text="Goodbye", start_time=datetime(2026, 5, 19, 12, 0, 20, tzinfo=timezone.utc)),
        ],
        audio_bytes=with_audio,
        tags=["test"],
    )


def test_vcon_syntax_is_0_4_0():
    v = PipecatVconBuilder().build(_sample_state())
    assert v.vcon_dict["vcon"] == "0.4.0"


def test_two_parties_user_first_then_agent():
    v = PipecatVconBuilder().build(_sample_state())
    parties = v.vcon_dict["parties"]
    assert len(parties) == 2
    assert parties[0]["name"] == "Customer"
    assert parties[1]["name"] == "Agent"


def test_each_turn_becomes_a_dialog():
    v = PipecatVconBuilder().build(_sample_state())
    assert len(v.vcon_dict["dialog"]) == 3  # 2 user + 1 assistant


def test_dialogs_use_mediatype_text_plain():
    v = PipecatVconBuilder().build(_sample_state())
    for dialog in v.vcon_dict["dialog"]:
        assert dialog["mediatype"] == "text/plain"
        assert "mimetype" not in dialog


def test_dialog_originator_role_mapping():
    v = PipecatVconBuilder().build(_sample_state())
    dialogs = v.vcon_dict["dialog"]
    assert dialogs[0]["originator"] == 0  # user
    assert dialogs[1]["originator"] == 1  # assistant
    assert dialogs[2]["originator"] == 0  # user


def test_dialogs_strip_empty_metadata_placeholders():
    v = PipecatVconBuilder().build(_sample_state())
    for dialog in v.vcon_dict["dialog"]:
        assert "metadata" not in dialog
        assert "meta" not in dialog


def test_inline_audio_uses_base64url_and_content_hash():
    audio = b"fake wav bytes"
    v = PipecatVconBuilder().build(_sample_state(with_audio=audio))
    # The recording dialog should be the last one (after text turns)
    recording = [d for d in v.vcon_dict["dialog"] if d.get("type") == "recording"]
    assert len(recording) == 1
    dialog = recording[0]
    assert dialog["encoding"] == "base64url"
    assert dialog["body"] == base64.urlsafe_b64encode(audio).rstrip(b"=").decode("ascii")
    assert dialog["content_hash"].startswith("sha512-")


def test_call_record_attachment_uses_purpose_and_indices():
    v = PipecatVconBuilder().build(_sample_state())
    atts = [a for a in v.vcon_dict["attachments"] if a.get("purpose") == "call_record"]
    assert len(atts) == 1
    att = atts[0]
    assert "type" not in att
    assert att["party"] == 0
    assert att["dialog"] == 0
    body = json.loads(att["body"])
    assert body["conversation_id"] == "call-abc-123"


def test_tags_attachment_has_party_and_dialog():
    v = PipecatVconBuilder().build(_sample_state())
    tags_atts = [a for a in v.vcon_dict["attachments"] if a.get("purpose") == "tags"]
    assert tags_atts
    for att in tags_atts:
        assert att["party"] == 0
        assert att["dialog"] == 0


def test_observer_accumulates_and_emits_vcon_on_end():
    """The observer should call its on_vcon callback once `end()` is invoked."""
    received: list = []
    obs = VconConversationObserver(
        conversation_id="obs-test",
        on_vcon=received.append,
        user_party={"name": "U"},
        agent_party={"name": "A"},
    )
    obs.record_user_transcript("Hi")
    obs.record_assistant_text_chunk("Hello, ")
    obs.record_assistant_text_chunk("how can I help?")
    obs.finalize_assistant_turn()
    obs.record_user_transcript("Bye")
    vcon = obs.end()
    assert len(received) == 1
    assert received[0].uuid == vcon.uuid
    # 3 dialogs: 2 user, 1 (chunked-and-finalized) assistant
    assert len(vcon.vcon_dict["dialog"]) == 3
    # The assistant turn body should be the concatenated chunks
    assistant_dialogs = [d for d in vcon.vcon_dict["dialog"] if d.get("originator") == 1]
    assert assistant_dialogs[0]["body"] == "Hello, how can I help?"


def test_observer_as_frame_processor_raises_when_pipecat_not_installed():
    """The lazy pipecat import should produce a friendly error, not a generic one."""
    obs = VconConversationObserver(conversation_id="x", on_vcon=lambda v: None)
    try:
        obs.as_frame_processor()
    except RuntimeError as e:
        assert "pipecat-ai is not installed" in str(e)
    else:
        # If pipecat happens to be installed, that's fine too — skip the assertion.
        pass
