"""vCon builder for VAPI end-of-call-report messages.

VAPI (https://vapi.ai) is a voice-AI agent platform. Its `end-of-call-report`
server message carries the whole conversation at once (per-turn messages,
transcript, analysis, recording URL), unlike the recording-webhook adapters
(Twilio, Bandwidth, ...) which get one event per recording. That shape does
not fit `core.base_builder.BaseVconBuilder` (single recording dialog, single
download), so this builder is written directly against vcon-lib, following
the same conventions `BaseVconBuilder` uses: lawful basis is applied via
`LawfulBasisConfig`, audio is re-hosted through the configured
`AudioPublisher` rather than embedded when one is configured, `mediatype`
(never `mimetype`) is used throughout, and every attachment gets the
draft-04 required fields (`start`, `party`, `dialog`, and `mediatype` when a
`body` is present) backfilled before the vCon is returned.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

from core.lawful_basis import LawfulBasisConfig
from core.media_publisher import AudioPublisher, PublishingError

logger = logging.getLogger(__name__)


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sha512_content_hash(data: bytes) -> str:
    return "sha512-" + _base64url(hashlib.sha512(data).digest())


def _epoch_ms_to_iso(epoch_ms: Any) -> str | None:
    if epoch_ms is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch_ms) / 1000.0).isoformat()
    except (ValueError, TypeError):
        return None


def _strip_empty_placeholders(d: dict, keys: tuple = ("meta", "metadata")) -> None:
    """vcon-lib's Dialog.to_dict emits empty `{}` placeholders when unset."""
    for k in keys:
        if k in d and d[k] == {}:
            del d[k]


class VapiVconBuilder:
    """Builds a vCon from a VAPI `end-of-call-report` message payload."""

    ADAPTER_SOURCE = "vapi"

    def __init__(
        self,
        *,
        customer_party_name: str = "Customer",
        agent_party_name: str = "AI Agent",
        download_recordings: bool = True,
        publisher: AudioPublisher | None = None,
        lawful_basis: LawfulBasisConfig | None = None,
    ):
        self.customer_party_name = customer_party_name
        self.agent_party_name = agent_party_name
        self.download_recordings = download_recordings
        self.publisher = publisher
        self.lawful_basis = lawful_basis

    def build(self, vapi_message: dict[str, Any]) -> Vcon | None:
        """Build a vCon from a VAPI 'end-of-call-report' message payload.

        Returns None for any other message type (status updates, etc.) so the
        webhook handler can ack and ignore it.
        """
        try:
            if vapi_message.get("type") != "end-of-call-report":
                logger.debug("Ignoring non-end-of-call VAPI message")
                return None

            vcon = Vcon.build_new()

            if vapi_message.get("startedAt"):
                vcon.vcon_dict["created_at"] = vapi_message["startedAt"]

            vcon.add_party(Party(name=self.customer_party_name, role="customer"))
            vcon.add_party(Party(name=self.agent_party_name, role="agent"))

            artifact = vapi_message.get("artifact") or {}
            for msg in artifact.get("messages", []) or []:
                role = msg.get("role")
                if role not in ("user", "bot"):
                    continue
                start_iso = _epoch_ms_to_iso(msg.get("time"))
                vcon.add_dialog(
                    Dialog(
                        type="text",
                        start=start_iso,
                        parties=[0, 1],
                        originator=0 if role == "user" else 1,
                        body=msg.get("message", ""),
                        encoding="none",
                        mediatype="text/plain",
                    )
                )
                _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

            self._add_recording_dialog(vcon, vapi_message)

            if vapi_message.get("transcript"):
                vcon.add_analysis(
                    type="transcript",
                    dialog=0,
                    vendor="vapi",
                    body=vapi_message["transcript"],
                    encoding="none",
                )

            analysis_block = vapi_message.get("analysis") or {}
            if analysis_block.get("summary"):
                vcon.add_analysis(
                    type="summary",
                    dialog=0,
                    vendor="vapi",
                    body=analysis_block["summary"],
                    encoding="none",
                )
            if "successEvaluation" in analysis_block:
                vcon.add_analysis(
                    type="success_evaluation",
                    dialog=0,
                    vendor="vapi",
                    body=json.dumps({"value": analysis_block["successEvaluation"]}),
                    encoding="json",
                )

            call = vapi_message.get("call")
            call_id = call.get("id") if isinstance(call, dict) else None
            call_record = {
                k: v
                for k, v in {
                    "cost": vapi_message.get("cost"),
                    "durationSeconds": vapi_message.get("durationSeconds"),
                    "endedReason": vapi_message.get("endedReason"),
                    "callId": call_id,
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

            vcon.add_tag("source", self.ADAPTER_SOURCE)
            if call_id:
                vcon.add_tag("call_id", call_id)

            if self.lawful_basis is None or not self.lawful_basis.apply(vcon):
                logger.warning(
                    "vCon %s carries no lawful_basis attachment. Set LAWFUL_BASIS "
                    "before handling real conversations.",
                    vcon.uuid,
                )

            # Backfill draft-04 required attachment fields the library
            # doesn't set on its own (see core.base_builder.build for the
            # equivalent, kept in step with it here since this builder
            # doesn't subclass BaseVconBuilder).
            for attachment in vcon.vcon_dict.get("attachments", []):
                attachment.setdefault("start", vcon.created_at)
                attachment.setdefault("party", 0)
                attachment.setdefault("dialog", 0)
                if attachment.get("body") is not None:
                    attachment.setdefault("mediatype", "application/json")

            return vcon

        except Exception as e:  # noqa: BLE001 - a build failure must not crash the webhook
            logger.error(f"Error building VAPI vCon: {e}")
            return None

    def _add_recording_dialog(self, vcon: Vcon, vapi_message: dict[str, Any]) -> None:
        recording_url = vapi_message.get("recordingUrl")
        if not recording_url:
            return

        call = vapi_message.get("call")
        call_id = (call.get("id") if isinstance(call, dict) else None) or "vapi-call"
        filename = f"{call_id}.wav"
        duration = vapi_message.get("durationSeconds")

        dialog_kwargs: dict[str, Any] = {
            "type": "recording",
            "start": _epoch_ms_to_iso(vapi_message.get("startedAt"))
            or vapi_message.get("startedAt"),
            "parties": [0, 1],
            "mediatype": "audio/wav",
        }
        if duration is not None:
            dialog_kwargs["duration"] = duration

        if self.publisher is not None:
            published = self._download_and_publish(recording_url, filename)
            if published is not None:
                dialog_kwargs["url"] = published[0]
                dialog_kwargs["content_hash"] = published[1]
                dialog_kwargs["filename"] = filename
            else:
                logger.error(
                    "Publishing failed for VAPI call %s; emitting no recording dialog "
                    "rather than a link that will not resolve",
                    call_id,
                )
                return
        elif self.download_recordings:
            audio_bytes = self._download(recording_url)
            if audio_bytes:
                dialog_kwargs["body"] = _base64url(audio_bytes)
                dialog_kwargs["encoding"] = "base64url"
                dialog_kwargs["content_hash"] = _sha512_content_hash(audio_bytes)
                dialog_kwargs["filename"] = filename
            else:
                dialog_kwargs["url"] = recording_url
        else:
            dialog_kwargs["url"] = recording_url

        vcon.add_dialog(Dialog(**dialog_kwargs))
        _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

    @staticmethod
    def _download(recording_url: str) -> bytes | None:
        import requests

        try:
            response = requests.get(recording_url, timeout=60)
            response.raise_for_status()
            return response.content
        except requests.RequestException as e:
            logger.error(f"Failed to download VAPI recording: {e}")
            return None

    def _download_and_publish(self, recording_url: str, filename: str) -> tuple[str, str] | None:
        audio_bytes = self._download(recording_url)
        if not audio_bytes:
            return None

        tmp_dir = Path(tempfile.mkdtemp(prefix="vcon-media-"))
        tmp_path = tmp_dir / filename
        try:
            tmp_path.write_bytes(audio_bytes)
            published = self.publisher.publish(tmp_path, filename)
            return published.url, published.content_hash
        except PublishingError as exc:
            logger.error(f"Publishing VAPI recording failed: {exc}")
            return None
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
