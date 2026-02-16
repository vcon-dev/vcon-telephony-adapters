"""vCon builder for Asterisk recordings."""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
from requests.auth import HTTPBasicAuth

from core.base_builder import BaseRecordingData, BaseVconBuilder


logger = logging.getLogger(__name__)


class AsteriskRecordingData(BaseRecordingData):
    """Recording data from Asterisk ARI/AMI event.

    Asterisk can send recording events via:
    - ARI (Asterisk REST Interface) webhooks
    - AMI (Asterisk Manager Interface) events
    - Custom AGI scripts

    Expected webhook fields:
    - recording_name: Name of the recording
    - target_uri: URI where recording is stored
    - format: Recording format (wav, gsm, etc.)
    - duration: Recording duration in seconds
    - channel_id: Asterisk channel ID
    - caller_id_num: Caller's number
    - caller_id_name: Caller's name (optional)
    - connected_line_num: Connected party's number
    - state: Recording state (done, failed, etc.)
    - timestamp: Event timestamp
    """

    def __init__(self, event_data: Dict[str, Any]):
        """Initialize from Asterisk event data.

        Args:
            event_data: Dictionary of Asterisk ARI/AMI event data
        """
        self._data = event_data

    @property
    def recording_id(self) -> str:
        """Asterisk recording name/ID."""
        return self._data.get(
            "recording_name",
            self._data.get("name", self._data.get("Uniqueid", ""))
        )

    @property
    def from_number(self) -> str:
        """Caller's phone number."""
        return self._data.get(
            "caller_id_num",
            self._data.get("CallerIDNum", "")
        )

    @property
    def to_number(self) -> str:
        """Called phone number."""
        return self._data.get(
            "connected_line_num",
            self._data.get("ConnectedLineNum",
                          self._data.get("extension", ""))
        )

    @property
    def direction(self) -> str:
        """Call direction.

        Asterisk doesn't always provide direction directly,
        so we infer from context if needed.
        """
        direction = self._data.get("direction", "")
        if direction:
            return direction.lower()

        # Infer from channel context
        context = self._data.get("context", "")
        if "outbound" in context.lower() or "from-internal" in context.lower():
            return "outbound"
        return "inbound"

    @property
    def recording_url(self) -> str:
        """URL or path to the recording."""
        return self._data.get(
            "target_uri",
            self._data.get("recording_url", "")
        )

    @property
    def recording_file_path(self) -> Optional[str]:
        """Local file path to the recording."""
        target_uri = self._data.get("target_uri", "")
        if target_uri.startswith("file:"):
            return target_uri.replace("file:", "")

        recording_name = self.recording_id
        if recording_name:
            return recording_name

        return None

    @property
    def recording_format(self) -> str:
        """Recording file format."""
        return self._data.get("format", "wav")

    @property
    def duration_seconds(self) -> Optional[float]:
        """Recording duration in seconds."""
        duration = self._data.get("duration")
        if duration is not None:
            try:
                return float(duration)
            except (ValueError, TypeError):
                return None
        return None

    @property
    def start_time(self) -> datetime:
        """Recording start time."""
        # Try timestamp field
        timestamp = self._data.get("timestamp")
        if timestamp:
            try:
                return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Try Unix timestamp
        start_epoch = self._data.get("start_time")
        if start_epoch:
            try:
                return datetime.fromtimestamp(float(start_epoch), tz=timezone.utc)
            except (ValueError, TypeError):
                pass

        return datetime.now(timezone.utc)

    @property
    def platform_tags(self) -> Dict[str, str]:
        """Asterisk-specific metadata tags."""
        tags = {
            "asterisk_recording_name": self.recording_id,
        }

        if self._data.get("channel_id"):
            tags["asterisk_channel_id"] = self._data["channel_id"]

        if self._data.get("Uniqueid"):
            tags["asterisk_unique_id"] = self._data["Uniqueid"]

        if self._data.get("caller_id_name"):
            tags["caller_name"] = self._data["caller_id_name"]

        if self._data.get("context"):
            tags["asterisk_context"] = self._data["context"]

        if self._data.get("application"):
            tags["asterisk_application"] = self._data["application"]

        return tags


class AsteriskVconBuilder(BaseVconBuilder):
    """Build vCons from Asterisk recording events.

    Supports:
    - ARI recording downloads
    - Local file reads
    - HTTP URL downloads
    """

    ADAPTER_SOURCE = "asterisk_adapter"

    def __init__(
        self,
        download_recordings: bool = True,
        recording_format: str = "wav",
        recordings_path: Optional[str] = None,
        ari_url: Optional[str] = None,
        ari_auth: Optional[tuple] = None,
    ):
        """Initialize Asterisk vCon builder.

        Args:
            download_recordings: Whether to download/read and embed recordings
            recording_format: Preferred recording format (wav or mp3)
            recordings_path: Base path for local recording files
            ari_url: Asterisk REST Interface base URL
            ari_auth: Tuple of (username, password) for ARI auth
        """
        super().__init__(download_recordings, recording_format)
        self.recordings_path = recordings_path or "/var/spool/asterisk/recording"
        self.ari_url = ari_url
        self.ari_auth = ari_auth

    def _download_recording(
        self,
        recording_data: BaseRecordingData
    ) -> Optional[bytes]:
        """Download or read recording from Asterisk.

        Args:
            recording_data: Asterisk recording data

        Returns:
            Raw audio bytes or None if download/read fails
        """
        ast_data = recording_data
        if not isinstance(ast_data, AsteriskRecordingData):
            logger.error("Expected AsteriskRecordingData")
            return None

        # Try ARI endpoint first
        if self.ari_url and ast_data.recording_id:
            try:
                url = f"{self.ari_url}/recordings/stored/{ast_data.recording_id}/file"
                logger.debug(f"Downloading recording from ARI: {url}")

                auth = HTTPBasicAuth(*self.ari_auth) if self.ari_auth else None
                response = requests.get(url, auth=auth, timeout=60)
                response.raise_for_status()
                return response.content
            except requests.RequestException as e:
                logger.warning(f"Failed to download from ARI: {e}")

        # Try HTTP URL if provided
        recording_url = ast_data.recording_url
        if recording_url.startswith(("http://", "https://")):
            try:
                logger.debug(f"Downloading recording from URL: {recording_url}")
                response = requests.get(recording_url, timeout=60)
                response.raise_for_status()
                return response.content
            except requests.RequestException as e:
                logger.warning(f"Failed to download from URL: {e}")

        # Try local file path
        file_path = ast_data.recording_file_path
        if file_path:
            # Handle relative paths
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.recordings_path, file_path)

            # Ensure correct extension
            recording_fmt = ast_data.recording_format or self.recording_format
            if not file_path.endswith(f".{recording_fmt}"):
                base_path = os.path.splitext(file_path)[0]
                file_path = f"{base_path}.{recording_fmt}"

            try:
                logger.debug(f"Reading recording from file: {file_path}")
                with open(file_path, "rb") as f:
                    return f.read()
            except IOError as e:
                logger.error(f"Failed to read recording file: {e}")

        logger.error(
            f"Could not access recording for {ast_data.recording_id}"
        )
        return None
