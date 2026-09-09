"""vCon builder to create vCons from Twilio recordings."""

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from core.base_builder import BaseRecordingData, BaseVconBuilder
from core.media_publisher import AudioPublisher

logger = logging.getLogger(__name__)


class TwilioRecordingData(BaseRecordingData):
    """Data class to hold Twilio recording webhook data."""

    def __init__(self, webhook_data: dict[str, Any]):
        """Initialize from Twilio webhook payload.

        Args:
            webhook_data: Dictionary of Twilio webhook parameters
        """
        # Core identifiers
        self.recording_sid = webhook_data.get("RecordingSid", "")
        self.account_sid = webhook_data.get("AccountSid", "")
        self.call_sid = webhook_data.get("CallSid", "")

        # Recording details
        self._recording_url = webhook_data.get("RecordingUrl", "")
        self.recording_status = webhook_data.get("RecordingStatus", "")
        self.recording_duration = webhook_data.get("RecordingDuration")
        self.recording_channels = webhook_data.get("RecordingChannels", "1")
        self.recording_source = webhook_data.get("RecordingSource", "")
        self.recording_start_time = webhook_data.get("RecordingStartTime")

        # Call participants
        self._from_number = webhook_data.get("From", "")
        self._to_number = webhook_data.get("To", "")
        self.caller = webhook_data.get("Caller", self._from_number)
        self.called = webhook_data.get("Called", self._to_number)

        # Direction and status
        self._direction = webhook_data.get("Direction", "")
        self.call_status = webhook_data.get("CallStatus", "")

        # Additional metadata
        self.api_version = webhook_data.get("ApiVersion", "")
        self.forwarded_from = webhook_data.get("ForwardedFrom")
        self.caller_city = webhook_data.get("CallerCity")
        self.caller_state = webhook_data.get("CallerState")
        self.caller_zip = webhook_data.get("CallerZip")
        self.caller_country = webhook_data.get("CallerCountry")
        self.called_city = webhook_data.get("CalledCity")
        self.called_state = webhook_data.get("CalledState")
        self.called_zip = webhook_data.get("CalledZip")
        self.called_country = webhook_data.get("CalledCountry")

        # Store raw data for additional fields
        self._raw_data = webhook_data

    # BaseRecordingData abstract property implementations

    @property
    def recording_id(self) -> str:
        """Platform-specific recording identifier."""
        return self.recording_sid

    @property
    def from_number(self) -> str:
        """Caller's phone number."""
        return self._from_number

    @property
    def to_number(self) -> str:
        """Callee's phone number."""
        return self._to_number

    @property
    def direction(self) -> str:
        """Call direction."""
        return self._direction

    @property
    def recording_url(self) -> str:
        """URL to download the recording."""
        return self._recording_url

    @property
    def duration_seconds(self) -> float | None:
        """Get recording duration in seconds."""
        if self.recording_duration:
            try:
                return float(self.recording_duration)
            except (ValueError, TypeError):
                return None
        return None

    @property
    def start_time(self) -> datetime:
        """Get recording start time as datetime."""
        if self.recording_start_time:
            try:
                # Twilio sends timestamps in RFC 2822 format
                from email.utils import parsedate_to_datetime

                return parsedate_to_datetime(self.recording_start_time)
            except Exception:
                pass
        return datetime.now(timezone.utc)

    @property
    def platform_tags(self) -> dict[str, str]:
        """Twilio-specific tags to add to the vCon."""
        tags = {
            "recording_sid": self.recording_sid,
            "call_sid": self.call_sid,
            "account_sid": self.account_sid,
        }

        if self._direction:
            tags["direction"] = self._direction

        if self.recording_source:
            tags["recording_source"] = self.recording_source

        if self.duration_seconds is not None:
            tags["duration_seconds"] = f"{self.duration_seconds:.2f}"

        # Add geographic metadata if available
        if self.caller_city:
            tags["caller_city"] = self.caller_city
        if self.caller_state:
            tags["caller_state"] = self.caller_state
        if self.caller_country:
            tags["caller_country"] = self.caller_country
        if self.called_city:
            tags["called_city"] = self.called_city
        if self.called_state:
            tags["called_state"] = self.called_state
        if self.called_country:
            tags["called_country"] = self.called_country

        return tags


class TwilioVconBuilder(BaseVconBuilder):
    """Builds vCon objects from Twilio recording data."""

    ADAPTER_SOURCE = "twilio_adapter"

    def __init__(
        self,
        download_recordings: bool = True,
        recording_format: str = "wav",
        twilio_auth: tuple | None = None,
        publisher: AudioPublisher | None = None,
    ):
        """Initialize builder.

        Args:
            download_recordings: Whether to download and embed recording audio
            recording_format: Preferred format for recordings (wav or mp3)
            twilio_auth: Tuple of (account_sid, auth_token) for Twilio API
        """
        super().__init__(download_recordings, recording_format, publisher)
        self.twilio_auth = twilio_auth

    def _download_recording(self, recording_data: BaseRecordingData) -> bytes | None:
        """Download recording audio from Twilio.

        Args:
            recording_data: Recording data with URL

        Returns:
            Raw audio bytes or None if download fails
        """
        recording_url = recording_data.recording_url
        if not recording_url:
            return None

        # Append format extension to get the audio file
        url = f"{recording_url}.{self.recording_format}"

        try:
            response = requests.get(url, auth=self.twilio_auth, timeout=60)

            if response.status_code == 200:
                logger.debug(f"Downloaded recording: {len(response.content)} bytes")
                return response.content
            else:
                logger.error(
                    f"Failed to download recording from {url}: " f"status {response.status_code}"
                )
                return None

        except Exception as e:
            logger.error(f"Error downloading recording from {url}: {e}")
            return None


# Backwards compatibility alias
VconBuilder = TwilioVconBuilder
