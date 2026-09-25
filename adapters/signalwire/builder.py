"""SignalWire vCon builder.

SignalWire is polled (see `poller.py`), and one poll cycle can return
multiple Recording objects for the same call, so this builder produces a
single vCon with multiple dialogs rather than one vCon per recording. That
shape doesn't fit `core.base_builder.BaseVconBuilder` (single recording,
single download), so, like the VAPI/Pipecat/ElevenLabs builders, it is
written directly against vcon-lib but follows the same conventions:
`LawfulBasisConfig` for the lawful-basis attachment, the configured
`AudioPublisher` for re-hosting audio instead of always embedding it,
`mediatype` throughout, and the draft-04 attachment-field backfill.
"""

from __future__ import annotations

import email.utils
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

from core.lawful_basis import LawfulBasisConfig
from core.media_publisher import AudioPublisher, PublishingError

logger = logging.getLogger(__name__)


@dataclass
class SignalWireRecordingData:
    """Normalized representation of a SignalWire poller result for one call."""

    call_meta: dict[str, Any]
    recordings: list[dict[str, Any]]
    transcriptions_by_recording_sid: dict[str, list[dict[str, Any]]]

    @property
    def call_sid(self) -> str:
        return self.call_meta.get("sid", "")


def _format_to_e164(phone_number: str | None) -> str | None:
    """Strip non-digits and prepend '+' (US-biased, matching SignalWire's
    Twilio-compatible number formatting)."""
    if not phone_number:
        return None
    digits = "".join(c for c in str(phone_number) if c.isdigit())
    if not digits:
        return None
    if len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}"


def _parse_rfc2822_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        logger.warning(f"Failed to parse SignalWire date_created: {value!r}")
        return None


def _strip_empty_placeholders(d: dict, keys: tuple = ("meta", "metadata")) -> None:
    for k in keys:
        if k in d and d[k] == {}:
            del d[k]


def _as_float(value: Any) -> float | None:
    """SignalWire's Recordings.json returns `duration` as a numeric string
    (e.g. "30"), not a number; the vCon spec's dialog `duration` is numeric,
    so this coerces rather than passing the string straight through."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning(f"Could not parse SignalWire recording duration: {value!r}")
        return None


class SignalWireVconBuilder:
    """Builds one vCon per SignalWire call, with one dialog per recording."""

    ADAPTER_SOURCE = "signalwire"
    TRANSCRIPT_SCHEMA = "https://docs.signalwire.com/reference/compatibility-api/v1/transcriptions"

    def __init__(
        self,
        *,
        download_recordings: bool = True,
        api_auth: tuple[str, str] | None = None,
        publisher: AudioPublisher | None = None,
        lawful_basis: LawfulBasisConfig | None = None,
    ):
        self.download_recordings = download_recordings
        self.api_auth = api_auth
        self.publisher = publisher
        self.lawful_basis = lawful_basis

    def build(self, data: SignalWireRecordingData) -> Vcon | None:
        """Produce one vCon containing all of the call's recordings as dialogs."""
        try:
            call_meta = data.call_meta

            vcon = Vcon.build_new()
            vcon.add_party(Party(tel=_format_to_e164(call_meta.get("to_formatted"))))
            vcon.add_party(Party(tel=_format_to_e164(call_meta.get("from_formatted"))))

            for dialog_idx, recording in enumerate(data.recordings):
                self._add_recording(vcon, recording, dialog_idx)

                for transcription in data.transcriptions_by_recording_sid.get(
                    recording.get("sid", ""), []
                ):
                    if "text" in transcription:
                        vcon.add_analysis(
                            type="transcript",
                            dialog=dialog_idx,
                            vendor="signalwire",
                            product="signalwire-transcription",
                            schema=self.TRANSCRIPT_SCHEMA,
                            body=json.dumps(transcription),
                            encoding="json",
                        )

            vcon.add_tag("source", self.ADAPTER_SOURCE)
            if data.call_sid:
                vcon.add_tag("call_sid", data.call_sid)

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

        except Exception as e:  # noqa: BLE001
            logger.error(f"Error building SignalWire vCon: {e}")
            return None

    def _add_recording(self, vcon: Vcon, recording: dict[str, Any], dialog_idx: int) -> None:
        start_iso = _parse_rfc2822_to_iso(recording.get("date_created"))
        recording_url = recording.get("recording_url") or recording.get("uri")
        recording_sid = recording.get("sid", f"recording-{dialog_idx}")
        filename = f"{recording_sid}.wav"

        dialog_kwargs: dict[str, Any] = {
            "start": start_iso,
            "parties": [0, 1],
            "type": "recording",
            "duration": _as_float(recording.get("duration")),
            "mediatype": "audio/wav",
        }

        if recording_url and self.publisher is not None:
            published = self._download_and_publish(recording_url, filename)
            if published is not None:
                dialog_kwargs["url"] = published[0]
                dialog_kwargs["content_hash"] = published[1]
                dialog_kwargs["filename"] = filename
            else:
                logger.error(
                    "Publishing failed for SignalWire recording %s; emitting no url "
                    "rather than a link that will not resolve",
                    recording_sid,
                )
        elif recording_url:
            dialog_kwargs["url"] = recording_url
            if self.download_recordings:
                logger.debug(
                    "DOWNLOAD_RECORDINGS is set but no publisher is configured; "
                    "SignalWire dialogs reference the recording URL directly "
                    "(the recording metadata attachment records the source)."
                )

        vcon.add_dialog(Dialog(**dialog_kwargs))
        _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

        vcon.add_attachment(
            purpose="recording_metadata",
            body=json.dumps(
                {
                    "sid": recording.get("sid"),
                    "account_sid": recording.get("account_sid"),
                    "call_sid": recording.get("call_sid"),
                    "channels": recording.get("channels"),
                    "source": "SignalWire",
                }
            ),
            encoding="json",
            party=0,
            dialog=dialog_idx,
        )

    def _download_and_publish(self, recording_url: str, filename: str) -> tuple[str, str] | None:
        try:
            response = requests.get(recording_url, auth=self.api_auth, timeout=60)
            response.raise_for_status()
            audio_bytes = response.content
        except requests.RequestException as e:
            logger.error(f"Failed to download SignalWire recording: {e}")
            return None

        tmp_dir = Path(tempfile.mkdtemp(prefix="vcon-media-"))
        tmp_path = tmp_dir / filename
        try:
            tmp_path.write_bytes(audio_bytes)
            published = self.publisher.publish(tmp_path, filename)
            return published.url, published.content_hash
        except PublishingError as exc:
            logger.error(f"Publishing SignalWire recording failed: {exc}")
            return None
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
