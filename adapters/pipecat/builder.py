"""Pipecat vCon builder.

Converts accumulated Pipecat conversation state into a spec-compliant vCon
(IETF draft-ietf-vcon-vcon-core-02, syntax 0.4.0). Pipecat doesn't have a single
"end-of-call payload" like VAPI; the integration accumulates state across frame
events and builds the vCon when the conversation ends.

Use this directly (e.g. from your own observer) or via the `VconConversationObserver`
in `observer.py` which wraps it as a Pipecat FrameProcessor.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

logger = logging.getLogger(__name__)


@dataclass
class PipecatTurn:
    """One turn of a Pipecat conversation — user or assistant utterance."""

    role: Literal["user", "assistant"]
    text: str
    start_time: datetime
    end_time: datetime | None = None


@dataclass
class PipecatConversationState:
    """Accumulated state for a single Pipecat conversation."""

    conversation_id: str
    started_at: datetime
    ended_at: datetime | None = None
    user_party: dict | None = None     # e.g. {"name": "...", "tel": "...", "mailto": "..."}
    agent_party: dict | None = None    # e.g. {"name": "Agent", "role": "agent"}
    turns: list[PipecatTurn] = field(default_factory=list)
    audio_bytes: bytes | None = None
    audio_mediatype: str = "audio/wav"
    metadata: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sha512_content_hash(data: bytes) -> str:
    return "sha512-" + _base64url(hashlib.sha512(data).digest())


def _strip_empty_placeholders(d: dict, keys: tuple = ("meta", "metadata")) -> None:
    for k in keys:
        if k in d and d[k] == {}:
            del d[k]


class PipecatVconBuilder:
    ADAPTER_SOURCE = "pipecat"

    def build(self, state: PipecatConversationState) -> Vcon:
        vcon = Vcon.build_new()
        vcon.vcon_dict["created_at"] = state.started_at.isoformat()
        if state.ended_at:
            vcon.vcon_dict["updated_at"] = state.ended_at.isoformat()

        # Parties (user index 0, agent index 1).
        user_kwargs = state.user_party or {"role": "user"}
        agent_kwargs = state.agent_party or {"name": "Agent", "role": "agent"}
        vcon.add_party(Party(**user_kwargs))
        vcon.add_party(Party(**agent_kwargs))

        # One text dialog per turn; one audio dialog if audio bytes captured.
        for turn in state.turns:
            vcon.add_dialog(Dialog(
                type="text",
                start=turn.start_time,
                parties=[0, 1],
                originator=0 if turn.role == "user" else 1,
                body=turn.text,
                encoding="none",
                mediatype="text/plain",
                duration=(
                    (turn.end_time - turn.start_time).total_seconds()
                    if turn.end_time else None
                ),
            ))
            _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

        if state.audio_bytes:
            vcon.add_dialog(Dialog(
                type="recording",
                start=state.started_at,
                parties=[0, 1],
                mediatype=state.audio_mediatype,
                body=_base64url(state.audio_bytes),
                encoding="base64url",
                content_hash=_sha512_content_hash(state.audio_bytes),
                duration=(
                    (state.ended_at - state.started_at).total_seconds()
                    if state.ended_at else None
                ),
            ))
            _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

        # Call record attachment.
        body = {"conversation_id": state.conversation_id}
        if state.metadata:
            body["metadata"] = state.metadata
        vcon.add_attachment(
            purpose="call_record",
            body=json.dumps(body),
            encoding="json",
            party=0,
            dialog=0,
        )

        # Tags.
        vcon.add_tag("source", self.ADAPTER_SOURCE)
        vcon.add_tag("conversation_id", state.conversation_id)
        for tag in state.tags:
            vcon.add_tag("user_tag", tag)

        return vcon
