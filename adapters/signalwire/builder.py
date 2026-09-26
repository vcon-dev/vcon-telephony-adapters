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

CON-1105 (recording-set): when a call has more than one recording segment,
draft-ietf-vcon-vcon-core-04 Sec. 4.3.1.2/4.3.6/4.3.7 says the per-segment
"recording" dialogs SHOULD be grouped under a "recording-set" dialog whose
own `recordings` array lists their indices, and whose `start`/`duration`/
`parties` describe the call as a whole rather than any one segment; each
member recording SHOULD carry a `recording_set` index back to it. The
installed vcon-lib (pypi `vcon` 0.9.6) predates that dialog type -
`Dialog.VALID_TYPES` doesn't include "recording-set" - so it is added to the
class below at import time. `recordings`/`recording_set` are not in
vcon-lib's `_ALLOWED_DIALOG_PROPERTIES` either, but `Vcon.build_new()` uses
the default (non-strict) property-handling mode, which keeps non-standard
Dialog properties as-is, so both fields still make it into the built vCon
dict unfiltered.
"""

from __future__ import annotations

import email.utils
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

from core.lawful_basis import LawfulBasisConfig
from core.media_publisher import AudioPublisher, PublishingError

logger = logging.getLogger(__name__)

# See the CON-1105 module docstring note above: vcon-lib 0.9.6 doesn't yet
# know about the draft-04 "recording-set" dialog type.
if "recording-set" not in Dialog.VALID_TYPES:
    Dialog.VALID_TYPES.append("recording-set")


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

            if len(data.recordings) > 1:
                self._add_recording_set(vcon, data.recordings)

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

    def _add_recording_set(self, vcon: Vcon, recordings: list[dict[str, Any]]) -> None:
        """Append a "recording-set" dialog grouping the call's segment
        recordings, per draft-ietf-vcon-vcon-core-04 Sec. 4.3.1.2/4.3.6/4.3.7.

        Every recording dialog for this call was already appended (in the
        same order as `recordings`) before this is called, so their dialog
        indices are simply `range(len(recordings))`; the recording-set dialog
        itself is appended last, one index past them.
        """
        recording_dialog_indices = list(range(len(recordings)))
        set_dialog_idx = len(vcon.vcon_dict["dialog"])

        starts = [_parse_rfc2822_to_iso(recording.get("date_created")) for recording in recordings]
        parsed_starts = [datetime.fromisoformat(s) for s in starts if s]
        call_start = min(parsed_starts).isoformat() if parsed_starts else None

        durations = [_as_float(recording.get("duration")) for recording in recordings]
        call_duration: float | None = None
        if parsed_starts and all(
            s and d is not None for s, d in zip(starts, durations, strict=True)
        ):
            ends = [
                datetime.fromisoformat(s) + timedelta(seconds=d)
                for s, d in zip(starts, durations, strict=True)
                if s and d is not None
            ]
            call_duration = (max(ends) - min(parsed_starts)).total_seconds()

        dialog_kwargs: dict[str, Any] = {
            "type": "recording-set",
            "recordings": recording_dialog_indices,
            # Both recording dialogs always carry parties=[0, 1] (see
            # _add_recording); the set's parties SHOULD list every party
            # known to the call as a whole (Sec. 4.3.4), which here is the
            # same superset regardless of which segments actually recorded.
            "parties": [0, 1],
        }
        if call_start:
            dialog_kwargs["start"] = call_start
        else:
            # Dialog.__init__ requires `start`; fall back to vcon creation
            # time rather than fabricating a call start we don't have.
            dialog_kwargs["start"] = vcon.created_at
        if call_duration is not None:
            dialog_kwargs["duration"] = call_duration

        vcon.add_dialog(Dialog(**dialog_kwargs))
        _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

        for idx in recording_dialog_indices:
            vcon.vcon_dict["dialog"][idx]["recording_set"] = set_dialog_idx

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
