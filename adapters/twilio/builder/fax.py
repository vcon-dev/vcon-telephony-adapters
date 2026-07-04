"""vCon builder for Twilio Fax status callbacks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests
from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

from .._common import (
    base64url_encode,
    determine_originator,
    sha512_content_hash,
    strip_empty_dict_placeholders,
)

logger = logging.getLogger(__name__)

FAX_MEDIATYPE = {
    "pdf": "application/pdf",
    "tiff": "image/tiff",
}


class TwilioFaxBuilder:
    """Build vCons from Twilio Fax webhooks."""

    ADAPTER_SOURCE = "twilio_adapter"

    def __init__(self, twilio_auth: tuple | None = None, download_fax: bool = True):
        self.twilio_auth = twilio_auth
        self.download_fax = download_fax

    def should_process(self, form: dict[str, Any]) -> bool:
        status = (form.get("Status") or form.get("FaxStatus") or "").lower()
        return status in ("received", "delivered", "completed") and bool(
            form.get("FaxSid") or form.get("MediaUrl")
        )

    def build(self, form: dict[str, Any]) -> Vcon | None:
        fax_sid = form.get("FaxSid", "")
        if not fax_sid:
            return None

        try:
            vcon = Vcon.build_new()
            from_num = form.get("From", "")
            to_num = form.get("To", "")

            vcon.add_party(Party(tel=from_num))
            vcon.add_party(Party(tel=to_num))

            start = datetime.now(timezone.utc)
            media_url = form.get("MediaUrl", "")
            mediatype = FAX_MEDIATYPE.get(
                (form.get("MediaFormat") or "pdf").lower(), "application/pdf"
            )

            dialog_kwargs: dict[str, Any] = {
                "type": "recording",
                "start": start,
                "parties": [0, 1],
                "originator": determine_originator(form.get("Direction", "inbound")),
                "mediatype": mediatype,
                "application": "twilio_fax",
                "session_id": fax_sid,
            }

            if self.download_fax and media_url:
                raw = self._download(media_url)
                if raw:
                    dialog_kwargs["body"] = base64url_encode(raw)
                    dialog_kwargs["encoding"] = "base64url"
                    dialog_kwargs["content_hash"] = sha512_content_hash(raw)
                    dialog_kwargs["filename"] = f"{fax_sid}.pdf"
                else:
                    dialog_kwargs["url"] = media_url
            elif media_url:
                dialog_kwargs["url"] = media_url

            vcon.add_dialog(Dialog(**dialog_kwargs))
            strip_empty_dict_placeholders(vcon.vcon_dict["dialog"][-1])

            vcon.add_tag("source", self.ADAPTER_SOURCE)
            vcon.add_tag("communication_mode", "Fax")
            vcon.add_tag("fax_sid", fax_sid)
            if form.get("NumPages"):
                vcon.add_tag("pages", str(form["NumPages"]))
            if form.get("AccountSid"):
                vcon.add_tag("account_sid", form["AccountSid"])

            return vcon

        except Exception as e:
            logger.error(f"Error building fax vCon for {fax_sid}: {e}")
            return None

    def _download(self, url: str) -> bytes | None:
        try:
            response = requests.get(url, auth=self.twilio_auth, timeout=60)
            if response.status_code == 200:
                return response.content
            logger.error(f"Fax download failed ({response.status_code}): {url}")
        except Exception as e:
            logger.error(f"Fax download error: {e}")
        return None
