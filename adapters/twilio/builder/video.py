"""vCon builder for Twilio Video composition/recording callbacks."""

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
    sha512_content_hash,
    strip_empty_dict_placeholders,
)

logger = logging.getLogger(__name__)


class TwilioVideoBuilder:
    """Build vCons from Twilio Video room recording/composition webhooks."""

    ADAPTER_SOURCE = "twilio_adapter"

    def __init__(
        self,
        twilio_auth: tuple | None = None,
        download_video: bool = True,
        account_sid: str | None = None,
    ):
        self.twilio_auth = twilio_auth
        self.download_video = download_video
        self.account_sid = account_sid

    def should_process(self, form: dict[str, Any]) -> bool:
        event = (form.get("StatusCallbackEvent") or form.get("Status") or "").lower()
        return event in (
            "composition-available",
            "composition-enqueued",
            "recording-completed",
            "completed",
        )

    def build(self, form: dict[str, Any]) -> Vcon | None:
        room_sid = form.get("RoomSid", "")
        composition_sid = form.get("CompositionSid") or form.get("RecordingSid", "")
        if not composition_sid and not room_sid:
            return None

        try:
            vcon = Vcon.build_new()
            # Video rooms may not have tel parties; use room name when available
            room_name = form.get("RoomName", room_sid or "video_room")
            vcon.add_party(Party(name=room_name, role="room"))
            vcon.add_party(Party(name="twilio_video", role="platform"))

            start = datetime.now(timezone.utc)
            media_url = self._resolve_media_url(form)

            dialog_kwargs: dict[str, Any] = {
                "type": "video",
                "start": start,
                "parties": [0, 1],
                "originator": 0,
                "mediatype": "video/mp4",
                "application": "twilio_video",
                "session_id": room_sid or composition_sid,
            }

            if self.download_video and media_url:
                raw = self._download(media_url)
                if raw:
                    dialog_kwargs["body"] = base64url_encode(raw)
                    dialog_kwargs["encoding"] = "base64url"
                    dialog_kwargs["content_hash"] = sha512_content_hash(raw)
                    dialog_kwargs["filename"] = f"{composition_sid or room_sid}.mp4"
                elif media_url:
                    dialog_kwargs["url"] = media_url
            elif media_url:
                dialog_kwargs["url"] = media_url

            vcon.add_dialog(Dialog(**dialog_kwargs))
            strip_empty_dict_placeholders(vcon.vcon_dict["dialog"][-1])

            vcon.add_tag("source", self.ADAPTER_SOURCE)
            vcon.add_tag("communication_mode", "WebRTC")
            if room_sid:
                vcon.add_tag("room_sid", room_sid)
            if composition_sid:
                vcon.add_tag("composition_sid", composition_sid)
            if form.get("AccountSid"):
                vcon.add_tag("account_sid", form["AccountSid"])

            return vcon

        except Exception as e:
            logger.error(f"Error building video vCon: {e}")
            return None

    def _resolve_media_url(self, form: dict[str, Any]) -> str | None:
        if form.get("MediaUri"):
            return form["MediaUri"]
        if form.get("Url"):
            return form["Url"]
        composition_sid = form.get("CompositionSid")
        account_sid = form.get("AccountSid") or self.account_sid
        if composition_sid and account_sid:
            return (
                f"https://video.twilio.com/v1/Compositions/{composition_sid}/Media"
                f"?Ttl=3600"
            )
        return None

    def _download(self, url: str) -> bytes | None:
        try:
            response = requests.get(url, auth=self.twilio_auth, timeout=120)
            if response.status_code == 200:
                return response.content
            logger.error(f"Video download failed ({response.status_code}): {url}")
        except Exception as e:
            logger.error(f"Video download error: {e}")
        return None
