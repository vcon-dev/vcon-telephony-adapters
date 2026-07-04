"""vCon builder for Twilio voice recording status callbacks."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from core.base_builder import BaseRecordingData, BaseVconBuilder

from .._common import strip_empty_dict_placeholders

logger = logging.getLogger(__name__)


class TwilioRecordingData(BaseRecordingData):
    """Data class to hold Twilio recording webhook data."""

    def __init__(self, webhook_data: dict[str, Any]):
        self.recording_sid = webhook_data.get("RecordingSid", "")
        self.account_sid = webhook_data.get("AccountSid", "")
        self.call_sid = webhook_data.get("CallSid", "")

        self._recording_url = webhook_data.get("RecordingUrl", "")
        self.recording_status = webhook_data.get("RecordingStatus", "")
        self.recording_duration = webhook_data.get("RecordingDuration")
        self.recording_channels = webhook_data.get("RecordingChannels", "1")
        self.recording_source = webhook_data.get("RecordingSource", "")
        self.recording_start_time = webhook_data.get("RecordingStartTime")

        self._from_number = webhook_data.get("From", "")
        self._to_number = webhook_data.get("To", "")
        self.caller = webhook_data.get("Caller", self._from_number)
        self.called = webhook_data.get("Called", self._to_number)

        self._direction = webhook_data.get("Direction", "")
        self.call_status = webhook_data.get("CallStatus", "")

        self.api_version = webhook_data.get("ApiVersion", "")
        self.forwarded_from = webhook_data.get("ForwardedFrom")
        self.parent_call_sid = webhook_data.get("ParentCallSid")
        self.caller_city = webhook_data.get("CallerCity")
        self.caller_state = webhook_data.get("CallerState")
        self.caller_zip = webhook_data.get("CallerZip")
        self.caller_country = webhook_data.get("CallerCountry")
        self.called_city = webhook_data.get("CalledCity")
        self.called_state = webhook_data.get("CalledState")
        self.called_zip = webhook_data.get("CalledZip")
        self.called_country = webhook_data.get("CalledCountry")

        self.stir_verstat = webhook_data.get("StirVerstat")
        self.stir_passport_token = webhook_data.get("StirPassportToken")
        self.call_token = webhook_data.get("CallToken")

        self._raw_data = webhook_data

    @property
    def recording_id(self) -> str:
        return self.recording_sid

    @property
    def from_number(self) -> str:
        return self._from_number

    @property
    def to_number(self) -> str:
        return self._to_number

    @property
    def direction(self) -> str:
        return self._direction

    @property
    def recording_url(self) -> str:
        return self._recording_url

    @property
    def duration_seconds(self) -> float | None:
        if self.recording_duration:
            try:
                return float(self.recording_duration)
            except (ValueError, TypeError):
                return None
        return None

    @property
    def start_time(self) -> datetime:
        if self.recording_start_time:
            try:
                return parsedate_to_datetime(self.recording_start_time)
            except Exception:
                pass
        return datetime.now(timezone.utc)

    @property
    def platform_tags(self) -> dict[str, str]:
        tags = {
            "recording_sid": self.recording_sid,
            "call_sid": self.call_sid,
            "account_sid": self.account_sid,
            "communication_mode": "PSTN",
        }

        if self._direction:
            tags["direction"] = self._direction

        if self.recording_source:
            tags["recording_source"] = self.recording_source

        if self.duration_seconds is not None:
            tags["duration_seconds"] = f"{self.duration_seconds:.2f}"

        if self.parent_call_sid:
            tags["parent_call_sid"] = self.parent_call_sid

        for key, val in (
            ("caller_city", self.caller_city),
            ("caller_state", self.caller_state),
            ("caller_country", self.caller_country),
            ("called_city", self.called_city),
            ("called_state", self.called_state),
            ("called_country", self.called_country),
        ):
            if val:
                tags[key] = val

        if self.stir_verstat:
            tags["stir_verstat"] = self.stir_verstat

        return tags


class TwilioVconBuilder(BaseVconBuilder):
    """Builds vCon objects from Twilio voice recording data."""

    ADAPTER_SOURCE = "twilio_adapter"

    def __init__(
        self,
        download_recordings: bool = True,
        recording_format: str = "wav",
        twilio_auth: tuple | None = None,
    ):
        super().__init__(download_recordings, recording_format)
        self.twilio_auth = twilio_auth

    def build(self, recording_data: BaseRecordingData) -> Any:
        vcon = super().build(recording_data)
        if not vcon:
            return None

        dialog = vcon.vcon_dict["dialog"][-1]
        dialog["application"] = "twilio_voice"
        if getattr(recording_data, "call_sid", None):
            dialog["session_id"] = recording_data.call_sid

        strip_empty_dict_placeholders(dialog)

        # Transfer dialog when ParentCallSid indicates a transferred leg
        parent_sid = getattr(recording_data, "parent_call_sid", None)
        if parent_sid:
            from vcon.dialog import Dialog

            transfer = Dialog(
                type="transfer",
                start=recording_data.start_time,
                transferor=0,
                transferee=1,
                transfer_target=1,
                original=0,
                target_dialog=len(vcon.vcon_dict["dialog"]) - 1,
            )
            vcon.add_dialog(transfer)
            strip_empty_dict_placeholders(vcon.vcon_dict["dialog"][-1])

        # STIR/SHAKEN as attachment when present
        stir_fields = {
            k: v
            for k, v in (
                ("StirVerstat", getattr(recording_data, "stir_verstat", None)),
                ("StirPassportToken", getattr(recording_data, "stir_passport_token", None)),
                ("CallToken", getattr(recording_data, "call_token", None)),
            )
            if v
        }
        if stir_fields:
            vcon.add_attachment(
                purpose="stir_shaken",
                body=json.dumps(stir_fields),
                encoding="json",
                party=0,
                dialog=0,
            )

        return vcon

    def _download_recording(self, recording_data: BaseRecordingData) -> bytes | None:
        recording_url = recording_data.recording_url
        if not recording_url:
            return None

        url = f"{recording_url}.{self.recording_format}"

        try:
            response = requests.get(url, auth=self.twilio_auth, timeout=60)

            if response.status_code == 200:
                logger.debug(f"Downloaded recording: {len(response.content)} bytes")
                return response.content
            logger.error(
                f"Failed to download recording from {url}: status {response.status_code}"
            )
            return None

        except Exception as e:
            logger.error(f"Error downloading recording from {url}: {e}")
            return None


# Backwards compatibility alias
VconBuilder = TwilioVconBuilder
