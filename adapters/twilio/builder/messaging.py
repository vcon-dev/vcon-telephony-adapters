"""vCon builder for Twilio SMS/MMS/WhatsApp and other messaging channels."""

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
    communication_mode_for_channel,
    detect_messaging_channel,
    determine_originator,
    sha512_content_hash,
    strip_empty_dict_placeholders,
)

logger = logging.getLogger(__name__)

APPLICATION_BY_CHANNEL = {
    "whatsapp": "twilio_whatsapp",
    "rcs": "twilio_rcs",
    "facebook": "twilio_facebook_messenger",
    "google": "twilio_google_business_messages",
    "mms": "twilio_mms",
    "sms": "twilio_sms",
}


class TwilioMessagingBuilder:
    """Build vCons from Twilio incoming message and status webhooks."""

    ADAPTER_SOURCE = "twilio_adapter"

    def __init__(self, twilio_auth: tuple | None = None, download_media: bool = True):
        self.twilio_auth = twilio_auth
        self.download_media = download_media

    def should_process_inbound(self, form: dict[str, Any]) -> bool:
        return bool(form.get("MessageSid") and form.get("Body") is not None)

    def should_process_status(self, form: dict[str, Any]) -> bool:
        # Status-only callbacks without Body still carry MessageSid
        return bool(form.get("MessageSid") and form.get("SmsStatus"))

    def build(self, form: dict[str, Any]) -> Vcon | None:
        message_sid = form.get("MessageSid", "")
        if not message_sid:
            return None

        try:
            vcon = Vcon.build_new()
            channel = detect_messaging_channel(form)
            mode = communication_mode_for_channel(channel)
            application = APPLICATION_BY_CHANNEL.get(channel, "twilio_messaging")

            from_num = form.get("From", "")
            to_num = form.get("To", "")
            profile_name = form.get("ProfileName")

            from_party = Party(tel=from_num, name=profile_name or None)
            vcon.add_party(from_party)
            vcon.add_party(Party(tel=to_num))

            direction = form.get("Direction", "inbound")
            start = datetime.now(timezone.utc)

            body_text = form.get("Body") or ""
            vcon.add_dialog(
                Dialog(
                    type="text",
                    start=start,
                    parties=[0, 1],
                    originator=determine_originator(direction),
                    body=body_text,
                    encoding="none",
                    mediatype="text/plain",
                    message_id=message_sid,
                    application=application,
                    session_id=form.get("MessagingServiceSid") or message_sid,
                )
            )
            strip_empty_dict_placeholders(vcon.vcon_dict["dialog"][-1])

            num_media = int(form.get("NumMedia") or "0")
            for i in range(num_media):
                media_url = form.get(f"MediaUrl{i}")
                content_type = form.get(f"MediaContentType{i}") or "application/octet-stream"
                if not media_url:
                    continue
                self._attach_media(vcon, media_url, content_type, index=i)

            vcon.add_tag("source", self.ADAPTER_SOURCE)
            vcon.add_tag("communication_mode", mode)
            vcon.add_tag("message_sid", message_sid)
            vcon.add_tag("channel", channel)
            if form.get("AccountSid"):
                vcon.add_tag("account_sid", form["AccountSid"])
            if form.get("SmsStatus"):
                vcon.add_tag("sms_status", form["SmsStatus"])

            return vcon

        except Exception as e:
            logger.error(f"Error building messaging vCon for {message_sid}: {e}")
            return None

    def _attach_media(self, vcon: Vcon, url: str, content_type: str, *, index: int) -> None:
        body: str
        encoding: str
        content_hash: str | None = None

        if self.download_media:
            try:
                response = requests.get(url, auth=self.twilio_auth, timeout=60)
                if response.status_code == 200:
                    raw = response.content
                    body = base64url_encode(raw)
                    encoding = "base64url"
                    content_hash = sha512_content_hash(raw)
                else:
                    logger.warning(f"MMS media download failed ({response.status_code}): {url}")
                    body = url
                    encoding = "none"
            except Exception as e:
                logger.warning(f"MMS media download error: {e}")
                body = url
                encoding = "none"
        else:
            body = url
            encoding = "none"

        kwargs: dict[str, Any] = {
            "purpose": "mms_media",
            "body": body,
            "encoding": encoding,
            "mediatype": content_type,
            "party": 0,
            "dialog": 0,
        }
        if content_hash:
            kwargs["content_hash"] = content_hash
        if encoding == "base64url":
            ext = content_type.split("/")[-1] if "/" in content_type else "bin"
            kwargs["filename"] = f"media_{index}.{ext}"

        vcon.add_attachment(**kwargs)
