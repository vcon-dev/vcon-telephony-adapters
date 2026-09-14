"""SignalWire vCon builder.

The shape is different from the webhook-style adapters (Twilio, Telnyx, …) because
SignalWire is polled — a single API call returns multiple Recording objects for
the same call, plus call metadata. Rather than building one vCon per recording,
we build a single vCon with multiple dialogs.

Spec target: IETF draft-ietf-vcon-vcon-core-02, syntax 0.4.0. Routes everything
through vcon-lib helpers (>=0.9.4) per the project's "always use lib helpers" rule.
"""

from __future__ import annotations

import email.utils
import json
import logging
from dataclasses import dataclass
from typing import Any

from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

logger = logging.getLogger(__name__)


@dataclass
class SignalWireRecordingData:
    """Normalized representation of a SignalWire poller result."""

    call_meta: dict[str, Any]
    recordings: list[dict[str, Any]]
    transcriptions_by_recording_sid: dict[str, list[dict[str, Any]]]

    @property
    def call_sid(self) -> str:
        return self.call_meta.get("sid", "")


def _format_to_e164(phone_number: str | None) -> str | None:
    """Strip non-digits and prepend '+' (US-biased)."""
    if not phone_number:
        return None
    digits = "".join(c for c in str(phone_number) if c.isdigit())
    if not digits:
        return None
    if len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}"


class SignalWireVconBuilder:
    """Builds a single spec-compliant vCon from a SignalWire call + recordings."""

    ADAPTER_SOURCE = "signalwire"
    TRANSCRIPT_SCHEMA = (
        "https://docs.signalwire.com/reference/compatibility-api/v1/transcriptions"
    )

    def build(self, data: SignalWireRecordingData) -> Vcon | None:
        """Produce one vCon containing all of the call's recordings as dialogs."""
        try:
            call_meta = data.call_meta

            vcon = Vcon.build_new()
            # Parties.
            vcon.add_party(Party(tel=_format_to_e164(call_meta.get("to_formatted"))))
            vcon.add_party(Party(tel=_format_to_e164(call_meta.get("from_formatted"))))

            # One dialog per recording.
            for dialog_idx, recording in enumerate(data.recordings):
                start_iso = _parse_rfc2822_to_iso(recording.get("date_created"))

                vcon.add_dialog(Dialog(
                    start=start_iso,
                    parties=[0, 1],
                    type="recording",
                    duration=recording.get("duration"),
                    url=recording.get("recording_url"),
                    mediatype="audio/wav",
                ))

                # Drop empty placeholders the lib's Dialog.to_dict leaves on every dialog.
                _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

                # Recording metadata attachment (JSON body via lib helper).
                vcon.add_attachment(
                    purpose="recording_metadata",
                    body=json.dumps({
                        "sid": recording.get("sid"),
                        "account_sid": recording.get("account_sid"),
                        "call_sid": recording.get("call_sid"),
                        "channels": recording.get("channels"),
                        "source": "SignalWire",
                    }),
                    encoding="json",
                    party=0,
                    dialog=dialog_idx,
                )

                # Transcripts go in analysis[], not attachments[]. Vendor REQUIRED.
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

            return vcon

        except Exception as e:
            logger.error(f"Error building SignalWire vCon: {e}")
            return None


def _parse_rfc2822_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        logger.warning(f"Failed to parse SignalWire date_created: {value!r}")
        return None


def _strip_empty_placeholders(d: dict, keys: tuple = ("meta", "metadata")) -> None:
    """vcon-lib Dialog.to_dict emits empty `{}` placeholders unset — strip them."""
    for k in keys:
        if k in d and d[k] == {}:
            del d[k]
