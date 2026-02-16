"""vCon builder for FreeSWITCH recordings."""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from core.base_builder import BaseRecordingData, BaseVconBuilder


logger = logging.getLogger(__name__)


class FreeSwitchRecordingData(BaseRecordingData):
    """Recording data from FreeSWITCH webhook/event.

    FreeSWITCH can send recording events via mod_http_cache,
    mod_event_socket, or custom Lua/dialplan scripts.

    Expected webhook fields:
    - uuid: Unique call UUID
    - recording_file: Path or filename of the recording
    - caller_id_number: Caller's number
    - caller_id_name: Caller's name (optional)
    - destination_number: Called number
    - direction: Call direction (inbound/outbound)
    - duration: Call duration in seconds
    - start_epoch: Call start time as Unix timestamp
    - record_seconds: Recording duration in seconds
    - recording_url: URL to download recording (if using HTTP storage)
    """

    def __init__(self, event_data: Dict[str, Any]):
        """Initialize from FreeSWITCH event data.

        Args:
            event_data: Dictionary of FreeSWITCH event/webhook data
        """
        self._data = event_data

    @property
    def recording_id(self) -> str:
        """FreeSWITCH call UUID."""
        return self._data.get("uuid", self._data.get("call_uuid", ""))

    @property
    def from_number(self) -> str:
        """Caller's phone number."""
        return self._data.get(
            "caller_id_number",
            self._data.get("Caller-Caller-ID-Number", "")
        )

    @property
    def to_number(self) -> str:
        """Called phone number."""
        return self._data.get(
            "destination_number",
            self._data.get("Caller-Destination-Number", "")
        )

    @property
    def direction(self) -> str:
        """Call direction."""
        direction = self._data.get(
            "direction",
            self._data.get("Caller-Direction", "inbound")
        )
        return direction.lower()

    @property
    def recording_url(self) -> str:
        """URL or path to the recording."""
        return self._data.get("recording_url", self._data.get("recording_file", ""))

    @property
    def recording_file_path(self) -> Optional[str]:
        """Local file path to the recording (if available)."""
        return self._data.get("recording_file", self._data.get("Record-File-Path"))

    @property
    def duration_seconds(self) -> Optional[float]:
        """Recording duration in seconds."""
        duration = self._data.get(
            "record_seconds",
            self._data.get("duration", self._data.get("variable_duration"))
        )
        if duration is not None:
            try:
                return float(duration)
            except (ValueError, TypeError):
                return None
        return None

    @property
    def start_time(self) -> datetime:
        """Recording start time."""
        # Try Unix timestamp first
        start_epoch = self._data.get(
            "start_epoch",
            self._data.get("Caller-Channel-Created-Time")
        )
        if start_epoch:
            try:
                # FreeSWITCH sometimes provides microseconds
                timestamp = int(start_epoch)
                if timestamp > 10**12:  # Microseconds
                    timestamp = timestamp // 1000000
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except (ValueError, TypeError):
                pass

        # Try ISO format
        start_str = self._data.get("start_time")
        if start_str:
            try:
                return datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return datetime.now(timezone.utc)

    @property
    def platform_tags(self) -> Dict[str, str]:
        """FreeSWITCH-specific metadata tags."""
        tags = {
            "freeswitch_uuid": self.recording_id,
        }

        # Add optional metadata
        if self._data.get("caller_id_name"):
            tags["caller_name"] = self._data["caller_id_name"]

        if self._data.get("accountcode"):
            tags["account_code"] = self._data["accountcode"]

        if self._data.get("context"):
            tags["freeswitch_context"] = self._data["context"]

        if self._data.get("sip_user_agent"):
            tags["sip_user_agent"] = self._data["sip_user_agent"]

        return tags


class FreeSwitchVconBuilder(BaseVconBuilder):
    """Build vCons from FreeSWITCH recording events.

    Supports both HTTP URL downloads and local file reads.
    """

    ADAPTER_SOURCE = "freeswitch_adapter"

    def __init__(
        self,
        download_recordings: bool = True,
        recording_format: str = "wav",
        recordings_path: Optional[str] = None,
        recordings_url_base: Optional[str] = None,
    ):
        """Initialize FreeSWITCH vCon builder.

        Args:
            download_recordings: Whether to download/read and embed recordings
            recording_format: Preferred recording format (wav or mp3)
            recordings_path: Base path for local recording files
            recordings_url_base: Base URL for HTTP recording access
        """
        super().__init__(download_recordings, recording_format)
        self.recordings_path = recordings_path or "/var/lib/freeswitch/recordings"
        self.recordings_url_base = recordings_url_base

    def _download_recording(
        self,
        recording_data: BaseRecordingData
    ) -> Optional[bytes]:
        """Download or read recording from FreeSWITCH.

        Args:
            recording_data: FreeSWITCH recording data

        Returns:
            Raw audio bytes or None if download/read fails
        """
        fs_data = recording_data
        if not isinstance(fs_data, FreeSwitchRecordingData):
            logger.error("Expected FreeSwitchRecordingData")
            return None

        # Try HTTP URL first
        recording_url = fs_data.recording_url
        if recording_url and recording_url.startswith(("http://", "https://")):
            try:
                logger.debug(f"Downloading recording from URL: {recording_url}")
                response = requests.get(recording_url, timeout=60)
                response.raise_for_status()
                return response.content
            except requests.RequestException as e:
                logger.warning(f"Failed to download from URL: {e}")

        # Try local file path
        file_path = fs_data.recording_file_path
        if file_path:
            # Handle relative paths
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.recordings_path, file_path)

            # Ensure correct extension
            if not file_path.endswith(f".{self.recording_format}"):
                base_path = os.path.splitext(file_path)[0]
                file_path = f"{base_path}.{self.recording_format}"

            try:
                logger.debug(f"Reading recording from file: {file_path}")
                with open(file_path, "rb") as f:
                    return f.read()
            except IOError as e:
                logger.error(f"Failed to read recording file: {e}")

        # Try constructing URL from base
        if self.recordings_url_base and recording_url:
            filename = os.path.basename(recording_url)
            full_url = f"{self.recordings_url_base.rstrip('/')}/{filename}"
            try:
                logger.debug(f"Trying constructed URL: {full_url}")
                response = requests.get(full_url, timeout=60)
                response.raise_for_status()
                return response.content
            except requests.RequestException as e:
                logger.warning(f"Failed to download from constructed URL: {e}")

        logger.error(
            f"Could not access recording for {fs_data.recording_id}"
        )
        return None
