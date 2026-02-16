"""vCon builder for Bandwidth recordings."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
from requests.auth import HTTPBasicAuth

from core.base_builder import BaseRecordingData, BaseVconBuilder


logger = logging.getLogger(__name__)


class BandwidthRecordingData(BaseRecordingData):
    """Recording data from Bandwidth webhook event.

    Bandwidth sends webhooks for voice recording events.

    Expected webhook structure for recordingComplete:
    {
        "eventType": "recordingComplete",
        "accountId": "123456",
        "applicationId": "app-id",
        "callId": "c-call-id",
        "callUrl": "https://voice.bandwidth.com/api/v2/accounts/123456/calls/c-call-id",
        "recordingId": "r-recording-id",
        "mediaUrl": "https://voice.bandwidth.com/api/v2/accounts/123456/calls/c-call-id/recordings/r-recording-id/media",
        "direction": "inbound" | "outbound",
        "from": "+15551234567",
        "to": "+15559876543",
        "startTime": "2024-01-15T10:29:30.000Z",
        "endTime": "2024-01-15T10:30:00.000Z",
        "duration": "PT30S",
        "channels": 1,
        "fileFormat": "wav" | "mp3",
        "status": "complete"
    }
    """

    def __init__(self, event_data: Dict[str, Any]):
        """Initialize from Bandwidth webhook event data.

        Args:
            event_data: Dictionary of Bandwidth webhook event
        """
        self._data = event_data

    @property
    def recording_id(self) -> str:
        """Bandwidth recording ID."""
        return self._data.get("recordingId", "")

    @property
    def call_id(self) -> str:
        """Bandwidth call ID."""
        return self._data.get("callId", "")

    @property
    def from_number(self) -> str:
        """Caller's phone number."""
        return self._data.get("from", "")

    @property
    def to_number(self) -> str:
        """Called phone number."""
        return self._data.get("to", "")

    @property
    def direction(self) -> str:
        """Call direction."""
        direction = self._data.get("direction", "inbound")
        return direction.lower()

    @property
    def recording_url(self) -> str:
        """URL to download the recording."""
        return self._data.get("mediaUrl", "")

    @property
    def file_format(self) -> str:
        """Recording file format."""
        return self._data.get("fileFormat", "wav")

    @property
    def duration_seconds(self) -> Optional[float]:
        """Recording duration in seconds.

        Bandwidth uses ISO 8601 duration format (e.g., "PT30S").
        """
        duration_str = self._data.get("duration", "")
        if not duration_str:
            return None

        # Parse ISO 8601 duration
        try:
            # Simple parser for common formats: PT30S, PT1M30S, PT1H30M45S
            import re
            pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?'
            match = re.match(pattern, duration_str)
            if match:
                hours = float(match.group(1) or 0)
                minutes = float(match.group(2) or 0)
                seconds = float(match.group(3) or 0)
                return hours * 3600 + minutes * 60 + seconds
        except (ValueError, AttributeError):
            pass

        return None

    @property
    def start_time(self) -> datetime:
        """Recording start time."""
        start_str = self._data.get("startTime")
        if start_str:
            try:
                return datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return datetime.now(timezone.utc)

    @property
    def end_time(self) -> Optional[datetime]:
        """Recording end time."""
        end_str = self._data.get("endTime")
        if end_str:
            try:
                return datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass
        return None

    @property
    def platform_tags(self) -> Dict[str, str]:
        """Bandwidth-specific metadata tags."""
        tags = {
            "bandwidth_recording_id": self.recording_id,
        }

        if self.call_id:
            tags["bandwidth_call_id"] = self.call_id

        if self._data.get("accountId"):
            tags["bandwidth_account_id"] = self._data["accountId"]

        if self._data.get("applicationId"):
            tags["bandwidth_application_id"] = self._data["applicationId"]

        if self._data.get("channels"):
            tags["recording_channels"] = str(self._data["channels"])

        if self._data.get("transcription"):
            tags["has_transcription"] = "true"

        return tags


class BandwidthVconBuilder(BaseVconBuilder):
    """Build vCons from Bandwidth recording events."""

    ADAPTER_SOURCE = "bandwidth_adapter"

    def __init__(
        self,
        download_recordings: bool = True,
        recording_format: str = "wav",
        api_auth: Optional[tuple] = None,
    ):
        """Initialize Bandwidth vCon builder.

        Args:
            download_recordings: Whether to download and embed recordings
            recording_format: Preferred recording format (wav or mp3)
            api_auth: Tuple of (username, password) for Bandwidth API auth
        """
        super().__init__(download_recordings, recording_format)
        self.api_auth = api_auth

    def _download_recording(
        self,
        recording_data: BaseRecordingData
    ) -> Optional[bytes]:
        """Download recording from Bandwidth.

        Args:
            recording_data: Bandwidth recording data

        Returns:
            Raw audio bytes or None if download fails
        """
        bw_data = recording_data
        if not isinstance(bw_data, BandwidthRecordingData):
            logger.error("Expected BandwidthRecordingData")
            return None

        recording_url = bw_data.recording_url
        if not recording_url:
            logger.error(f"No media URL for recording {bw_data.recording_id}")
            return None

        try:
            logger.debug(f"Downloading recording from: {recording_url}")

            auth = HTTPBasicAuth(*self.api_auth) if self.api_auth else None
            response = requests.get(
                recording_url,
                auth=auth,
                timeout=60
            )
            response.raise_for_status()
            return response.content

        except requests.RequestException as e:
            logger.error(
                f"Failed to download Bandwidth recording {bw_data.recording_id}: {e}"
            )
            return None
