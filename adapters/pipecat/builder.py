"""Pipecat vCon builder.

Converts accumulated Pipecat conversation state into a vCon. Pipecat doesn't
have a single "end-of-call payload" like a webhook adapter; state accumulates
across frame events (see `observer.py`) and the vCon is built when the
conversation ends. As with the VAPI adapter, this doesn't fit
`core.base_builder.BaseVconBuilder`'s single-recording-download shape, so it
is written directly against vcon-lib but follows the same conventions:
`LawfulBasisConfig` for the lawful-basis attachment, the configured
`AudioPublisher` for re-hosting captured audio instead of always embedding
it, `mediatype` throughout, and the draft-04 attachment-field backfill.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

from core.encoding import base64url_encode
from core.lawful_basis import LawfulBasisConfig
from core.media_publisher import AudioPublisher, PublishingError

logger = logging.getLogger(__name__)


@dataclass
class PipecatTurn:
    """One turn of a Pipecat conversation: a user or assistant utterance."""

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
    user_party: dict | None = None
    agent_party: dict | None = None
    turns: list[PipecatTurn] = field(default_factory=list)
    audio_bytes: bytes | None = None
    audio_mediatype: str = "audio/wav"
    metadata: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


def _sha512_content_hash(data: bytes) -> str:
    return "sha512-" + base64url_encode(hashlib.sha512(data).digest())


def _strip_empty_placeholders(d: dict, keys: tuple = ("meta", "metadata")) -> None:
    for k in keys:
        if k in d and d[k] == {}:
            del d[k]


class PipecatVconBuilder:
    ADAPTER_SOURCE = "pipecat"

    def __init__(
        self,
        *,
        publisher: AudioPublisher | None = None,
        lawful_basis: LawfulBasisConfig | None = None,
    ):
        self.publisher = publisher
        self.lawful_basis = lawful_basis

    def build(self, state: PipecatConversationState) -> Vcon:
        vcon = Vcon.build_new()
        vcon.vcon_dict["created_at"] = state.started_at.isoformat()
        if state.ended_at:
            vcon.vcon_dict["updated_at"] = state.ended_at.isoformat()

        user_kwargs = state.user_party or {"role": "user"}
        agent_kwargs = state.agent_party or {"name": "Agent", "role": "agent"}
        vcon.add_party(Party(**user_kwargs))
        vcon.add_party(Party(**agent_kwargs))

        for turn in state.turns:
            vcon.add_dialog(
                Dialog(
                    type="text",
                    start=turn.start_time,
                    parties=[0, 1],
                    originator=0 if turn.role == "user" else 1,
                    body=turn.text,
                    encoding="none",
                    mediatype="text/plain",
                    duration=(
                        (turn.end_time - turn.start_time).total_seconds() if turn.end_time else None
                    ),
                )
            )
            _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

        if state.audio_bytes:
            self._add_audio_dialog(vcon, state)

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

        vcon.add_tag("source", self.ADAPTER_SOURCE)
        vcon.add_tag("conversation_id", state.conversation_id)
        for tag in state.tags:
            vcon.add_tag("user_tag", tag)

        if self.lawful_basis is None or not self.lawful_basis.apply(vcon):
            logger.warning(
                "vCon %s carries no lawful_basis attachment. Set LAWFUL_BASIS "
                "before handling real conversations.",
                vcon.uuid,
            )

        for attachment in vcon.vcon_dict.get("attachments", []):
            attachment.setdefault("start", vcon.created_at)
            attachment.setdefault("party", 0)
            attachment.setdefault("dialog", 0)
            if attachment.get("body") is not None:
                attachment.setdefault("mediatype", "application/json")

        return vcon

    def _add_audio_dialog(self, vcon: Vcon, state: PipecatConversationState) -> None:
        duration = (state.ended_at - state.started_at).total_seconds() if state.ended_at else None
        extension = state.audio_mediatype.split("/")[-1] or "wav"
        filename = f"{state.conversation_id}.{extension}"

        dialog_kwargs: dict = {
            "type": "recording",
            "start": state.started_at,
            "parties": [0, 1],
            "mediatype": state.audio_mediatype,
        }
        if duration is not None:
            dialog_kwargs["duration"] = duration

        if self.publisher is not None:
            published = self._publish(state.audio_bytes, filename)
            if published is not None:
                dialog_kwargs["url"] = published[0]
                dialog_kwargs["content_hash"] = published[1]
                dialog_kwargs["filename"] = filename
            else:
                logger.error(
                    "Publishing failed for Pipecat conversation %s; emitting no "
                    "recording dialog rather than a link that will not resolve",
                    state.conversation_id,
                )
                return
        else:
            dialog_kwargs["body"] = base64url_encode(state.audio_bytes)
            dialog_kwargs["encoding"] = "base64url"
            dialog_kwargs["content_hash"] = _sha512_content_hash(state.audio_bytes)
            dialog_kwargs["filename"] = filename

        vcon.add_dialog(Dialog(**dialog_kwargs))
        _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

    def _publish(self, audio_bytes: bytes, filename: str) -> tuple[str, str] | None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="vcon-media-"))
        tmp_path = tmp_dir / filename
        try:
            tmp_path.write_bytes(audio_bytes)
            published = self.publisher.publish(tmp_path, filename)
            return published.url, published.content_hash
        except PublishingError as exc:
            logger.error(f"Publishing Pipecat audio failed: {exc}")
            return None
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
