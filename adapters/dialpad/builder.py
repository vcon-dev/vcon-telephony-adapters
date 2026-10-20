"""vCon builder for Dialpad call events.

Dialpad pushes a call event per state change (see
https://developers.dialpad.com/docs/call-events). The `recording` state
(recording finished processing), `call_transcription` (Dialpad AI transcript
ready) and `voicemail_uploaded` events can build a vCon. The event references
audio rather than carrying it, so this builder pulls it, plus the transcript
and AI recap, from the Dialpad API through `DialpadClient`.

A call can have several recordings (a callrecording plus, optionally, an
admincallrecording, or segments after a transfer), so one vCon is produced
with one dialog per recording. That does not fit
`core.base_builder.BaseVconBuilder` (single recording, single download), so,
like the SignalWire and VAPI builders, this is written directly against
vcon-lib and follows the same conventions: `LawfulBasisConfig` for the
lawful-basis attachment, the configured `AudioPublisher` for re-hosting audio,
`mediatype` throughout, and the draft-04 attachment-field backfill. When more
than one recording dialog is emitted they are grouped under a "recording-set"
dialog, the same pattern as `adapters/signalwire/builder.py` (CON-1105).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

from core.encoding import base64url_encode
from core.lawful_basis import LawfulBasisConfig
from core.media_publisher import AudioPublisher, PublishingError

from .client import DialpadClient

logger = logging.getLogger(__name__)

# vcon-lib 0.9.6 predates the draft-04 "recording-set" dialog type; see the
# matching note in adapters/signalwire/builder.py.
if "recording-set" not in Dialog.VALID_TYPES:
    Dialog.VALID_TYPES.append("recording-set")

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")


class DialpadBuildError(Exception):
    """A build failed for a reason worth retrying (API, download or publish).

    Distinct from `build()` returning None, which means the event has nothing
    to build (unsupported state, no recording on the call).
    """


BUILDABLE_STATES = ("recording", "call_transcription", "voicemail_uploaded")
MOMENT_LINE_TYPES = ("moment", "custom_moment", "real_time_moment", "ai_question")


def _sha512_content_hash(data: bytes) -> str:
    return "sha512-" + base64url_encode(hashlib.sha512(data).digest())


def _to_iso(value: Any) -> str | None:
    """Unix milliseconds or an ISO string to an ISO-8601 string, else None."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    try:
        text = str(value)
        if text.replace(".", "", 1).isdigit():
            return _to_iso(float(text))
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _ms_to_seconds(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return None


def _strip_empty_placeholders(d: dict, keys: tuple = ("meta", "metadata")) -> None:
    """vcon-lib's Dialog.to_dict emits empty `{}` placeholders when unset."""
    for k in keys:
        if k in d and d[k] == {}:
            del d[k]


def _make_party(info: dict[str, Any] | None, fallback_tel: str | None) -> Party:
    info = info or {}
    kwargs: dict[str, Any] = {}
    tel = info.get("phone") or fallback_tel
    if tel:
        kwargs["tel"] = tel
    if info.get("name"):
        kwargs["name"] = info["name"]
    if info.get("email"):
        kwargs["mailto"] = info["email"]
    return Party(**kwargs)


def _content_list(items: Any) -> list[str]:
    """Pull `content` strings out of a recap-proto list (or pass strings through)."""
    out: list[str] = []
    for item in items or []:
        content = item.get("content") if isinstance(item, dict) else item
        if content:
            out.append(str(content))
    return out


class DialpadVconBuilder:
    """Builds one vCon per Dialpad call, with one dialog per recording."""

    ADAPTER_SOURCE = "dialpad_adapter"

    def __init__(
        self,
        client: DialpadClient,
        publisher: AudioPublisher | None = None,
        lawful_basis: LawfulBasisConfig | None = None,
        include_transcript: bool = True,
        include_recap: bool = True,
        include_admin_recordings: bool = False,
        download_recordings: bool = True,
    ):
        self.client = client
        self.publisher = publisher
        self.lawful_basis = lawful_basis
        self.include_transcript = include_transcript
        self.include_recap = include_recap
        self.include_admin_recordings = include_admin_recordings
        self.download_recordings = download_recordings

    def build(self, event: dict[str, Any], allow_partial: bool = False) -> Vcon | None:
        """Build a vCon from a decoded Dialpad call event.

        Returns None when the event cannot produce a vCon (unsupported state
        or no recording on the call) so the webhook handler can ack and move
        on. Raises DialpadBuildError when the call has audio but it could not
        be fetched or published, so the caller can retry. A call with several
        recordings fails as a whole if any one of them fails, unless
        `allow_partial` (the caller's last attempt), in which case the
        recordings that did arrive are kept.
        """
        try:
            return self._build(event, allow_partial)
        except DialpadBuildError:
            raise
        except Exception as e:  # noqa: BLE001 - a build failure must not crash the webhook
            logger.error(f"Error building Dialpad vCon: {e}")
            raise DialpadBuildError(str(e)) from e

    def _build(self, event: dict[str, Any], allow_partial: bool) -> Vcon | None:
        state = event.get("state")
        if state not in BUILDABLE_STATES:
            logger.debug(f"Ignoring Dialpad event in state {state!r}")
            return None

        call_id = event.get("call_id")
        recordings = self._select_recordings(event)
        if (
            not event.get("recording_details")
            and state != "voicemail_uploaded"
            and call_id is not None
        ):
            # call_transcription events carry no recording_details; fetch them.
            call = self.client.get_call(call_id)
            if call is None:
                raise DialpadBuildError(f"could not fetch Dialpad call {call_id}")
            recordings = self._select_recordings(call)
        if not recordings:
            logger.info(f"Dialpad call {call_id} has no usable recording; skipping")
            return None

        vcon = Vcon.build_new()
        self._add_parties(vcon, event)

        recording_starts: list[str | None] = []
        recording_durations: list[float | None] = []
        failed = 0
        for recording in recordings:
            if self._add_recording(vcon, event, recording):
                recording_starts.append(self._recording_start(event, recording))
                recording_durations.append(_ms_to_seconds(recording.get("duration")))
            else:
                failed += 1

        if not vcon.vcon_dict.get("dialog") or (failed and not allow_partial):
            raise DialpadBuildError(
                f"Dialpad call {call_id}: {failed} of {len(recordings)} recordings "
                "could not be retrieved"
            )

        if len(vcon.vcon_dict["dialog"]) > 1:
            self._add_recording_set(vcon, recording_starts, recording_durations)

        # Transcript and recap are enrichment: a malformed or missing one
        # must not cost the recording.
        try:
            if state == "voicemail_uploaded":
                self._add_voicemail_transcript(vcon, event)
            elif self.include_transcript and call_id is not None:
                self._add_transcript(vcon, call_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Dialpad call {call_id}: skipping transcript: {e}")
        try:
            if self.include_recap and state != "voicemail_uploaded":
                self._add_recap(vcon, event, call_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Dialpad call {call_id}: skipping recap: {e}")

        self._add_tags(vcon, event)

        if self.lawful_basis is None or not self.lawful_basis.apply(vcon):
            logger.warning(
                "vCon %s carries no lawful_basis attachment. Set LAWFUL_BASIS "
                "before handling real conversations.",
                vcon.uuid,
            )

        # Backfill draft-04 required attachment fields the library
        # doesn't set on its own (kept in step with core.base_builder).
        for attachment in vcon.vcon_dict.get("attachments", []):
            attachment.setdefault("start", vcon.created_at)
            attachment.setdefault("party", 0)
            attachment.setdefault("dialog", 0)
            if attachment.get("body") is not None:
                attachment.setdefault("mediatype", "application/json")

        return vcon

    # -- recordings -------------------------------------------------------

    def _select_recordings(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        details = [d for d in (source.get("recording_details") or []) if isinstance(d, dict)]
        if not details and source.get("voicemail_link"):
            details = [{"url": source["voicemail_link"], "recording_type": "voicemail"}]
        return [
            d
            for d in details
            if self.include_admin_recordings or d.get("recording_type") != "admincallrecording"
        ]

    @staticmethod
    def _recording_start(event: dict[str, Any], recording: dict[str, Any]) -> str | None:
        return (
            _to_iso(recording.get("start_time"))
            or _to_iso(event.get("date_connected"))
            or _to_iso(event.get("date_started"))
        )

    def _add_parties(self, vcon: Vcon, event: dict[str, Any]) -> None:
        # Party 0 is the external contact: the subject of the recording, and
        # the party LawfulBasisConfig.apply references by default.
        vcon.add_party(_make_party(event.get("contact"), event.get("external_number")))
        vcon.add_party(_make_party(event.get("target"), event.get("internal_number")))

    def _add_recording(self, vcon: Vcon, event: dict[str, Any], recording: dict[str, Any]) -> bool:
        recording_id = str(
            recording.get("id") or f"{event.get('call_id')}-{len(vcon.vcon_dict.get('dialog', []))}"
        )
        # The id comes from the event; keep it from naming a path outside the
        # temp dir or the publisher's prefix.
        filename = f"{_UNSAFE_FILENAME_CHARS.sub('_', recording_id)}.mp3"
        # inbound: the external contact called in; outbound: the internal target did.
        originator = 1 if event.get("direction") == "outbound" else 0

        dialog_kwargs: dict[str, Any] = {
            "type": "recording",
            "start": self._recording_start(event, recording) or vcon.created_at,
            "parties": [0, 1],
            "originator": originator,
            "mediatype": "audio/mpeg",
        }
        duration = _ms_to_seconds(recording.get("duration"))
        if duration is not None:
            dialog_kwargs["duration"] = duration

        if not self.download_recordings and self.publisher is None:
            # Dialpad URLs need bearer auth, but a reference is still better
            # than nothing when the operator opted out of downloading.
            if not recording.get("url"):
                return False
            dialog_kwargs["url"] = recording["url"]
        else:
            audio = self.client.download_recording(recording)
            if not audio:
                logger.error(f"Dialpad recording {recording_id} could not be downloaded")
                return False
            if self.publisher is not None:
                published = self._publish(audio, filename)
                if published is None:
                    logger.error(
                        "Publishing failed for Dialpad recording %s; emitting no dialog "
                        "rather than a link that will not resolve",
                        recording_id,
                    )
                    return False
                dialog_kwargs["url"] = published[0]
                dialog_kwargs["content_hash"] = published[1]
            else:
                dialog_kwargs["body"] = base64url_encode(audio)
                dialog_kwargs["encoding"] = "base64url"
                dialog_kwargs["content_hash"] = _sha512_content_hash(audio)
            dialog_kwargs["filename"] = filename

        vcon.add_dialog(Dialog(**dialog_kwargs))
        _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])
        return True

    def _add_recording_set(
        self,
        vcon: Vcon,
        starts: list[str | None],
        durations: list[float | None],
    ) -> None:
        """Append a "recording-set" dialog grouping the call's recordings,
        per draft-ietf-vcon-vcon-core-04 Sec. 4.3.1.2/4.3.6/4.3.7.

        Every recording dialog was already appended before this is called,
        so their indices are `range(len(starts))`; the set dialog is appended
        last, one index past them.
        """
        recording_dialog_indices = list(range(len(starts)))
        set_dialog_idx = len(vcon.vcon_dict["dialog"])

        parsed_starts = [datetime.fromisoformat(s) for s in starts if s]
        call_start = min(parsed_starts).isoformat() if parsed_starts else None

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
            "parties": [0, 1],
            # Dialog.__init__ requires `start`; fall back to vcon creation
            # time rather than fabricating a call start we don't have.
            "start": call_start or vcon.created_at,
        }
        if call_duration is not None:
            dialog_kwargs["duration"] = call_duration

        vcon.add_dialog(Dialog(**dialog_kwargs))
        _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

        for idx in recording_dialog_indices:
            vcon.vcon_dict["dialog"][idx]["recording_set"] = set_dialog_idx

    def _publish(self, audio: bytes, filename: str) -> tuple[str, str] | None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="vcon-media-"))
        tmp_path = tmp_dir / filename
        try:
            tmp_path.write_bytes(audio)
            published = self.publisher.publish(tmp_path, filename)
            return published.url, published.content_hash
        except PublishingError as exc:
            logger.error(f"Publishing Dialpad recording failed: {exc}")
            return None
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # -- analysis ---------------------------------------------------------

    def _add_voicemail_transcript(self, vcon: Vcon, event: dict[str, Any]) -> None:
        text = event.get("transcription_text")
        if text:
            vcon.add_analysis(
                type="transcript",
                dialog=0,
                vendor="dialpad",
                product="dialpad-voicemail-transcription",
                body=text,
                encoding="none",
            )

    def _add_transcript(self, vcon: Vcon, call_id: Any) -> None:
        data = self.client.get_transcript(call_id)
        lines = (data or {}).get("lines") or []
        # Empty or missing is normal: transcripts need Dialpad AI on the call.
        transcript_lines = [ln for ln in lines if ln.get("type") == "transcript"]
        moment_lines = [ln for ln in lines if ln.get("type") in MOMENT_LINE_TYPES]
        if transcript_lines:
            vcon.add_analysis(
                type="transcript",
                dialog=0,
                vendor="dialpad",
                product="dialpad-ai-transcript",
                body=json.dumps(transcript_lines),
                encoding="json",
            )
        if moment_lines:
            vcon.add_analysis(
                type="moments",
                dialog=0,
                vendor="dialpad",
                product="dialpad-ai-transcript",
                body=json.dumps(moment_lines),
                encoding="json",
            )

    def _add_recap(self, vcon: Vcon, event: dict[str, Any], call_id: Any) -> None:
        summary = event.get("recap_summary")
        outcome = event.get("recap_outcome")
        purposes = _content_list(event.get("recap_purposes"))
        action_items = _content_list(event.get("recap_action_items"))

        if not (summary or outcome or purposes or action_items) and call_id is not None:
            # The event had no recap fields; ask the AI recap endpoint
            # (rate limited to 12 req/min, so only when the event is empty).
            recap = self.client.get_ai_recap(call_id) or {}
            summary = (recap.get("summary") or {}).get("content")
            outcome = (recap.get("disposition") or {}).get("content")
            purposes = _content_list(recap.get("purposes"))
            action_items = _content_list(recap.get("action_items"))

        common = {"dialog": 0, "vendor": "dialpad", "product": "dialpad-ai-recap"}
        if summary:
            vcon.add_analysis(type="summary", body=str(summary), encoding="none", **common)
        if outcome:
            vcon.add_analysis(type="outcome", body=str(outcome), encoding="none", **common)
        if purposes:
            vcon.add_analysis(type="purposes", body=json.dumps(purposes), encoding="json", **common)
        if action_items:
            vcon.add_analysis(
                type="action_items", body=json.dumps(action_items), encoding="json", **common
            )

    # -- tags -------------------------------------------------------------

    def _add_tags(self, vcon: Vcon, event: dict[str, Any]) -> None:
        vcon.add_tag("source", self.ADAPTER_SOURCE)
        for key in (
            "call_id",
            "master_call_id",
            "entry_point_call_id",
            "operator_call_id",
            "direction",
            "group_id",
        ):
            if event.get(key) not in (None, ""):
                vcon.add_tag(key, str(event[key]))
        if event.get("state"):
            vcon.add_tag("state", str(event["state"]))
