"""vCon builder for ElevenLabs Conversational AI post-call webhooks.

ElevenLabs sends two independent webhook types for a finished conversation
(https://elevenlabs.io/docs/conversational-ai/workflows/post-call-webhooks):

* `post_call_transcription` — `data.transcript[]` (per-turn role/message),
  `data.metadata` (start time, duration, cost), `data.analysis` (summary,
  call_successful). No audio.
* `post_call_audio` — `data.full_audio`, a base64-encoded MP3, plus only
  `agent_id`/`conversation_id`. No transcript or analysis. Delivered as a
  separate webhook call, not merged with the transcription payload.

Because the two arrive independently, this builder emits one vCon per
webhook rather than trying to merge them into a single vCon keyed on
`conversation_id`; both vCons carry a `conversation_id` tag so they can be
correlated downstream. This mirrors the rest of the monorepo (one inbound
event -> one vCon) rather than the polled, single-vCon-per-call shape the
earlier `rescue/cursor-multi-mode-wip` draft used.

Like the VAPI and Pipecat builders, this is written directly against
vcon-lib rather than `core.base_builder.BaseVconBuilder`, but follows the
same conventions: `LawfulBasisConfig`, the configured `AudioPublisher`,
`mediatype`, and the draft-04 attachment-field backfill.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

from core.encoding import base64url_encode
from core.lawful_basis import LawfulBasisConfig
from core.media_publisher import AudioPublisher, PublishingError

logger = logging.getLogger(__name__)


def _sha512_content_hash(data: bytes) -> str:
    return "sha512-" + base64url_encode(hashlib.sha512(data).digest())


def _epoch_to_iso(epoch_seconds: Any) -> str | None:
    if epoch_seconds is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return None


def _strip_empty_placeholders(d: dict, keys: tuple = ("meta", "metadata")) -> None:
    for k in keys:
        if k in d and d[k] == {}:
            del d[k]


class ElevenLabsVconBuilder:
    """Builds vCons from ElevenLabs post-call webhook payloads."""

    ADAPTER_SOURCE = "elevenlabs"

    def __init__(
        self,
        *,
        publisher: AudioPublisher | None = None,
        lawful_basis: LawfulBasisConfig | None = None,
    ):
        self.publisher = publisher
        self.lawful_basis = lawful_basis

    def build(self, event: dict[str, Any]) -> Vcon | None:
        """Build a vCon from one `post_call_transcription` or `post_call_audio`
        webhook event. Returns None for any other event type."""
        event_type = event.get("type")
        data = event.get("data") or {}

        if event_type == "post_call_transcription":
            return self._build_transcription(event, data)
        if event_type == "post_call_audio":
            return self._build_audio(event, data)

        logger.debug(f"Ignoring ElevenLabs event type: {event_type}")
        return None

    # ------------------------------------------------------------------
    def _build_transcription(self, event: dict[str, Any], data: dict[str, Any]) -> Vcon | None:
        try:
            vcon = Vcon.build_new()

            metadata = data.get("metadata") or {}
            start_iso = _epoch_to_iso(
                metadata.get("start_time_unix_secs") or event.get("event_timestamp")
            )
            if start_iso:
                vcon.vcon_dict["created_at"] = start_iso

            vcon.add_party(Party(role="user"))
            vcon.add_party(Party(name="Agent", role="agent"))

            for turn in data.get("transcript", []) or []:
                role = turn.get("role")
                if role not in ("user", "agent"):
                    continue
                turn_offset = turn.get("time_in_call_secs")
                turn_start = start_iso
                if start_iso is not None and turn_offset is not None:
                    try:
                        base = _epoch_to_iso(metadata.get("start_time_unix_secs"))
                        if base:
                            base_epoch = float(metadata["start_time_unix_secs"])
                            turn_start = _epoch_to_iso(base_epoch + float(turn_offset))
                    except (TypeError, ValueError):
                        pass
                vcon.add_dialog(
                    Dialog(
                        type="text",
                        start=turn_start,
                        parties=[0, 1],
                        originator=0 if role == "user" else 1,
                        body=turn.get("message", "") or "",
                        encoding="none",
                        mediatype="text/plain",
                    )
                )
                _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

            analysis = data.get("analysis") or {}
            if analysis.get("transcript_summary"):
                vcon.add_analysis(
                    type="summary",
                    dialog=0,
                    vendor="elevenlabs",
                    product="elevenlabs-conversational-ai",
                    body=analysis["transcript_summary"],
                    encoding="none",
                )
            if "call_successful" in analysis:
                vcon.add_analysis(
                    type="success_evaluation",
                    dialog=0,
                    vendor="elevenlabs",
                    body=json.dumps({"value": analysis["call_successful"]}),
                    encoding="json",
                )

            call_record = {
                k: v
                for k, v in {
                    "durationSeconds": metadata.get("call_duration_secs"),
                    "cost": metadata.get("cost"),
                    "status": data.get("status"),
                    "userId": data.get("user_id"),
                }.items()
                if v is not None
            }
            if call_record:
                vcon.add_attachment(
                    purpose="call_record",
                    body=json.dumps(call_record),
                    encoding="json",
                    party=0,
                    dialog=0,
                )

            self._finish(vcon, data)
            return vcon

        except Exception as e:  # noqa: BLE001
            logger.error(f"Error building ElevenLabs transcription vCon: {e}")
            return None

    def _build_audio(self, event: dict[str, Any], data: dict[str, Any]) -> Vcon | None:
        try:
            audio_b64 = data.get("full_audio")
            if not audio_b64:
                logger.warning("ElevenLabs post_call_audio event with no full_audio field")
                return None
            try:
                audio_bytes = base64.b64decode(audio_b64)
            except (binascii.Error, ValueError) as exc:
                logger.error(f"Could not decode ElevenLabs full_audio: {exc}")
                return None

            vcon = Vcon.build_new()
            start_iso = _epoch_to_iso(event.get("event_timestamp"))
            if start_iso:
                vcon.vcon_dict["created_at"] = start_iso

            vcon.add_party(Party(role="user"))
            vcon.add_party(Party(name="Agent", role="agent"))

            conversation_id = data.get("conversation_id") or "elevenlabs-conversation"
            filename = f"{conversation_id}.mp3"
            dialog_kwargs: dict[str, Any] = {
                "type": "recording",
                "start": start_iso,
                "parties": [0, 1],
                "mediatype": "audio/mpeg",
            }

            if self.publisher is not None:
                published = self._publish(audio_bytes, filename)
                if published is not None:
                    dialog_kwargs["url"] = published[0]
                    dialog_kwargs["content_hash"] = published[1]
                    dialog_kwargs["filename"] = filename
                else:
                    logger.error(
                        "Publishing failed for ElevenLabs conversation %s; emitting no "
                        "recording dialog rather than a link that will not resolve",
                        conversation_id,
                    )
                    return None
            else:
                dialog_kwargs["body"] = base64url_encode(audio_bytes)
                dialog_kwargs["encoding"] = "base64url"
                dialog_kwargs["content_hash"] = _sha512_content_hash(audio_bytes)
                dialog_kwargs["filename"] = filename

            vcon.add_dialog(Dialog(**dialog_kwargs))
            _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

            self._finish(vcon, data)
            return vcon

        except Exception as e:  # noqa: BLE001
            logger.error(f"Error building ElevenLabs audio vCon: {e}")
            return None

    # ------------------------------------------------------------------
    def _finish(self, vcon: Vcon, data: dict[str, Any]) -> None:
        vcon.add_tag("source", self.ADAPTER_SOURCE)
        if data.get("conversation_id"):
            vcon.add_tag("conversation_id", data["conversation_id"])
        if data.get("agent_id"):
            vcon.add_tag("agent_id", data["agent_id"])

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

    def _publish(self, audio_bytes: bytes, filename: str) -> tuple[str, str] | None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="vcon-media-"))
        tmp_path = tmp_dir / filename
        try:
            tmp_path.write_bytes(audio_bytes)
            published = self.publisher.publish(tmp_path, filename)
            return published.url, published.content_hash
        except PublishingError as exc:
            logger.error(f"Publishing ElevenLabs audio failed: {exc}")
            return None
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
