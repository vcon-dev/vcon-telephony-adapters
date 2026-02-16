"""vCon builder for Telnyx recordings."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from core.base_builder import BaseRecordingData, BaseVconBuilder


logger = logging.getLogger(__name__)


class TelnyxRecordingData(BaseRecordingData):
    """Recording data from Telnyx webhook event.

    Telnyx sends webhooks for call recording events with
    standardized event structure.

    Expected webhook structure:
    {
        "data": {
            "event_type": "call.recording.saved",
            "id": "event_id",
            "occurred_at": "2024-01-15T10:30:00Z",
            "payload": {
                "call_control_id": "uuid",
                "call_leg_id": "uuid",
                "call_session_id": "uuid",
                "connection_id": "uuid",
                "recording_id": "uuid",
                "recording_urls": {
                    "mp3": "https://...",
                    "wav": "https://..."
                },
                "channels": "single" | "dual",
                "duration_millis": 30000,
                "from": "+15551234567",
                "to": "+15559876543",
                "direction": "incoming" | "outgoing",
                "start_time": "2024-01-15T10:29:30Z"
            }
        }
    }
    """

    def __init__(self, event_data: Dict[str, Any]):
        """Initialize from Telnyx webhook event data.

        Args:
            event_data: Dictionary of Telnyx webhook event
        """
        # Handle nested structure
        if "data" in event_data and "payload" in event_data["data"]:
            self._payload = event_data["data"]["payload"]
            self._event = event_data["data"]
        elif "payload" in event_data:
            self._payload = event_data["payload"]
            self._event = event_data
        else:
            # Flat structure
            self._payload = event_data
            self._event = event_data

    @property
    def recording_id(self) -> str:
        """Telnyx recording ID."""
        return self._payload.get(
            "recording_id",
            self._payload.get("call_control_id", "")
        )

    @property
    def call_session_id(self) -> str:
        """Telnyx call session ID."""
        return self._payload.get("call_session_id", "")

    @property
    def from_number(self) -> str:
        """Caller's phone number."""
        return self._payload.get("from", "")

    @property
    def to_number(self) -> str:
        """Called phone number."""
        return self._payload.get("to", "")

    @property
    def direction(self) -> str:
        """Call direction."""
        direction = self._payload.get("direction", "incoming")
        # Telnyx uses "incoming"/"outgoing"
        if direction == "incoming":
            return "inbound"
        elif direction == "outgoing":
            return "outbound"
        return direction.lower()

    @property
    def recording_url(self) -> str:
        """URL to download the recording."""
        urls = self._payload.get("recording_urls", {})
        # Prefer wav, then mp3
        return urls.get("wav", urls.get("mp3", ""))

    @property
    def recording_urls(self) -> Dict[str, str]:
        """All available recording URLs by format."""
        return self._payload.get("recording_urls", {})

    @property
    def duration_seconds(self) -> Optional[float]:
        """Recording duration in seconds."""
        duration_millis = self._payload.get("duration_millis")
        if duration_millis is not None:
            try:
                return float(duration_millis) / 1000.0
            except (ValueError, TypeError):
                return None
        return None

    @property
    def start_time(self) -> datetime:
        """Recording start time."""
        start_str = self._payload.get("start_time")
        if start_str:
            try:
                return datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Fall back to event time
        occurred_at = self._event.get("occurred_at")
        if occurred_at:
            try:
                return datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return datetime.now(timezone.utc)

    @property
    def platform_tags(self) -> Dict[str, str]:
        """Telnyx-specific metadata tags."""
        tags = {
            "telnyx_recording_id": self.recording_id,
        }

        if self.call_session_id:
            tags["telnyx_call_session_id"] = self.call_session_id

        if self._payload.get("call_control_id"):
            tags["telnyx_call_control_id"] = self._payload["call_control_id"]

        if self._payload.get("connection_id"):
            tags["telnyx_connection_id"] = self._payload["connection_id"]

        if self._payload.get("channels"):
            tags["recording_channels"] = self._payload["channels"]

        return tags


class TelnyxVconBuilder(BaseVconBuilder):
    """Build vCons from Telnyx recording events."""

    ADAPTER_SOURCE = "telnyx_adapter"

    def __init__(
        self,
        download_recordings: bool = True,
        recording_format: str = "wav",
        api_key: Optional[str] = None,
    ):
        """Initialize Telnyx vCon builder.

        Args:
            download_recordings: Whether to download and embed recordings
            recording_format: Preferred recording format (wav or mp3)
            api_key: Telnyx API key for authenticated downloads
        """
        super().__init__(download_recordings, recording_format)
        self.api_key = api_key

    def _download_recording(
        self,
        recording_data: BaseRecordingData
    ) -> Optional[bytes]:
        """Download recording from Telnyx.

        Args:
            recording_data: Telnyx recording data

        Returns:
            Raw audio bytes or None if download fails
        """
        telnyx_data = recording_data
        if not isinstance(telnyx_data, TelnyxRecordingData):
            logger.error("Expected TelnyxRecordingData")
            return None

        # Get URL for preferred format
        urls = telnyx_data.recording_urls
        recording_url = urls.get(self.recording_format, telnyx_data.recording_url)

        if not recording_url:
            logger.error(f"No recording URL available for {telnyx_data.recording_id}")
            return None

        try:
            logger.debug(f"Downloading recording from: {recording_url}")

            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            response = requests.get(
                recording_url,
                headers=headers,
                timeout=60
            )
            response.raise_for_status()
            return response.content

        except requests.RequestException as e:
            logger.error(
                f"Failed to download Telnyx recording {telnyx_data.recording_id}: {e}"
            )
            return None
