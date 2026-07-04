"""Shared helpers for Twilio vCon builders."""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Twilio CallStatus -> vCon incomplete disposition (spec 4.3.x)
CALL_STATUS_TO_DISPOSITION: dict[str, str] = {
    "no-answer": "no-answer",
    "busy": "busy",
    "failed": "failed",
    "canceled": "hung-up",
}

# ChannelPrefix / messaging channel -> vCon communication_mode tag
CHANNEL_TO_MODE: dict[str, str] = {
    "whatsapp": "WhatsApp",
    "sms": "SMS",
    "mms": "MMS",
    "rcs": "RCS",
    "facebook": "FacebookMessenger",
    "google": "GoogleBusinessMessages",
}


def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sha512_content_hash(data: bytes) -> str:
    return "sha512-" + base64url_encode(hashlib.sha512(data).digest())


def strip_empty_dict_placeholders(d: dict, keys: tuple = ("meta", "metadata")) -> None:
    for k in keys:
        if k in d and d[k] == {}:
            del d[k]


def determine_originator(direction: str) -> int:
    if direction.lower() in ("outbound", "outbound-api", "outgoing"):
        return 0
    return 1


def map_call_status_to_disposition(call_status: str) -> str | None:
    status = call_status.lower().replace("_", "-")
    return CALL_STATUS_TO_DISPOSITION.get(status)


def detect_messaging_channel(form: dict[str, Any]) -> str:
    """Infer Twilio messaging channel from webhook fields."""
    prefix = (form.get("ChannelPrefix") or form.get("Channel") or "").lower()
    if prefix.startswith("whatsapp") or form.get("WaId"):
        return "whatsapp"
    if prefix == "rcs":
        return "rcs"
    if prefix in ("facebook", "messenger"):
        return "facebook"
    if prefix == "google":
        return "google"
    num_media = int(form.get("NumMedia") or "0")
    if num_media > 0:
        return "mms"
    return "sms"


def communication_mode_for_channel(channel: str) -> str:
    return CHANNEL_TO_MODE.get(channel, channel.upper())


def stir_shaken_attachment_fields(form: dict[str, Any]) -> dict[str, str]:
    """Extract STIR/SHAKEN fields when present on voice webhooks."""
    fields: dict[str, str] = {}
    for key in ("StirVerstat", "StirPassportToken", "CallToken"):
        if form.get(key):
            fields[key] = str(form[key])
    return fields
