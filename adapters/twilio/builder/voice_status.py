"""vCon builder for Twilio voice call status callbacks (no recording)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

from .._common import (
    determine_originator,
    map_call_status_to_disposition,
    strip_empty_dict_placeholders,
    stir_shaken_attachment_fields,
)

logger = logging.getLogger(__name__)

# Completed calls with recordings are handled by the recording webhook.
SKIP_STATUSES = frozenset({"completed", "in-progress", "ringing", "queued", "initiated"})


class TwilioVoiceStatusBuilder:
    """Build vCons from Twilio CallStatus webhooks when no recording exists."""

    ADAPTER_SOURCE = "twilio_adapter"

    def should_process(self, form: dict[str, Any]) -> bool:
        status = (form.get("CallStatus") or "").lower()
        if status in SKIP_STATUSES:
            return False
        return map_call_status_to_disposition(status) is not None

    def build(self, form: dict[str, Any]) -> Vcon | None:
        call_status = (form.get("CallStatus") or "").lower()
        disposition = map_call_status_to_disposition(call_status)
        if not disposition:
            logger.debug(f"Skipping voice status callback: {call_status}")
            return None

        try:
            vcon = Vcon.build_new()
            from_num = form.get("From", "")
            to_num = form.get("To", "")
            direction = form.get("Direction", "")

            vcon.add_party(Party(tel=from_num))
            vcon.add_party(Party(tel=to_num))

            start = datetime.now(timezone.utc)
            call_sid = form.get("CallSid", "")

            vcon.add_dialog(
                Dialog(
                    type="incomplete",
                    start=start,
                    parties=[0, 1],
                    originator=determine_originator(direction),
                    disposition=disposition,
                    application="twilio_voice",
                    session_id=call_sid or None,
                )
            )
            strip_empty_dict_placeholders(vcon.vcon_dict["dialog"][-1])

            vcon.add_tag("source", self.ADAPTER_SOURCE)
            vcon.add_tag("communication_mode", "PSTN")
            if call_sid:
                vcon.add_tag("call_sid", call_sid)
            if form.get("AccountSid"):
                vcon.add_tag("account_sid", form["AccountSid"])
            if form.get("ParentCallSid"):
                vcon.add_tag("parent_call_sid", form["ParentCallSid"])

            stir = stir_shaken_attachment_fields(form)
            if stir:
                vcon.add_attachment(
                    purpose="stir_shaken",
                    body=json.dumps(stir),
                    encoding="json",
                    party=0,
                    dialog=0,
                )

            return vcon

        except Exception as e:
            logger.error(f"Error building voice status vCon: {e}")
            return None
