"""ElevenLabs vCon builder.

Converts an ElevenLabs `Conversation` into a spec-compliant vCon
(IETF draft-ietf-vcon-vcon-core-02, syntax 0.4.0) via the vcon-lib helpers
(>=0.9.4). Builds parties, one or two dialogs (audio + optional transcript-as-text),
attachments for call metadata, and analysis entries for transcripts/summaries.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging

from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

from .models import Conversation

logger = logging.getLogger(__name__)


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sha512_content_hash(data: bytes) -> str:
    return "sha512-" + _base64url(hashlib.sha512(data).digest())


def _strip_empty_placeholders(d: dict, keys: tuple = ("meta", "metadata")) -> None:
    for k in keys:
        if k in d and d[k] == {}:
            del d[k]


class ElevenLabsVconBuilder:
    """Builds spec-compliant vCons from ElevenLabs Conversation objects.

    The builder does NOT fetch audio; if audio.url is set it's emitted as an
    external-media reference. If the caller has already downloaded the audio bytes,
    pass them to `build_with_audio` to get inline base64url + content_hash.
    """

    ADAPTER_SOURCE = "elevenlabs"
    TRANSCRIPT_SCHEMA = "https://elevenlabs.io/docs/conversational-ai"

    def build(self, conversation: Conversation, *, audio_bytes: bytes | None = None) -> Vcon:
        """Construct the vCon. Pass `audio_bytes` to embed inline."""
        vcon = Vcon.build_new()

        if conversation.start_time:
            vcon.vcon_dict["created_at"] = conversation.start_time.isoformat()

        self._add_parties(vcon, conversation)
        self._add_dialog(vcon, conversation, audio_bytes)
        self._add_call_record_attachment(vcon, conversation)
        self._add_transcript_analysis(vcon, conversation)
        self._add_tags(vcon, conversation)

        return vcon

    # ------------------------------------------------------------------
    def _add_parties(self, vcon: Vcon, conversation: Conversation) -> None:
        for p in conversation.participants:
            vcon.add_party(Party(
                name=p.name or None,
                tel=p.phone or None,
                mailto=p.email or None,
                role=p.role or None,
            ))

    def _add_dialog(
        self, vcon: Vcon, conversation: Conversation, audio_bytes: bytes | None
    ) -> None:
        audio = conversation.audio
        if not audio:
            return

        kwargs: dict = {
            "type": "recording",
            "start": conversation.start_time,
            "parties": list(range(len(conversation.participants))),
            "mediatype": f"audio/{audio.format}",
        }
        if conversation.duration_ms:
            kwargs["duration"] = conversation.duration_ms / 1000.0

        if audio_bytes:
            kwargs["body"] = _base64url(audio_bytes)
            kwargs["encoding"] = "base64url"
            kwargs["content_hash"] = _sha512_content_hash(audio_bytes)
            kwargs["filename"] = f"{conversation.id}.{audio.format}"
        elif audio.url:
            # External media — spec wants content_hash too; we don't have the bytes,
            # so emit url alone and log a warning.
            logger.warning(
                "ElevenLabs audio emitted as URL without content_hash; pass audio_bytes "
                "to build() for spec-compliant external media."
            )
            kwargs["url"] = audio.url

        vcon.add_dialog(Dialog(**kwargs))
        _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

    def _add_call_record_attachment(self, vcon: Vcon, conversation: Conversation) -> None:
        body = {
            "conversation_id": conversation.id,
            "agent_id": conversation.agent_id,
            "status": conversation.status,
            "duration_ms": conversation.duration_ms,
        }
        if conversation.metadata:
            body["metadata"] = conversation.metadata
        vcon.add_attachment(
            purpose="call_record",
            body=json.dumps(body),
            encoding="json",
            party=0,
            dialog=0,
        )

    def _add_transcript_analysis(self, vcon: Vcon, conversation: Conversation) -> None:
        if not conversation.transcript:
            return
        body = {
            "text": conversation.transcript.text,
            "language": conversation.transcript.language,
        }
        if conversation.transcript.confidence is not None:
            body["confidence"] = conversation.transcript.confidence
        vcon.add_analysis(
            type="transcript",
            dialog=0,
            vendor="elevenlabs",
            product="elevenlabs-conversational-ai",
            schema=self.TRANSCRIPT_SCHEMA,
            body=json.dumps(body),
            encoding="json",
        )

    def _add_tags(self, vcon: Vcon, conversation: Conversation) -> None:
        vcon.add_tag("source", self.ADAPTER_SOURCE)
        vcon.add_tag("conversation_id", conversation.id)
        vcon.add_tag("agent_id", conversation.agent_id)
        vcon.add_tag("status", conversation.status)
        for tag in conversation.tags:
            vcon.add_tag("user_tag", tag)
