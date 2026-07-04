"""Base vCon builder with common functionality for all telephony adapters."""

import base64
import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

logger = logging.getLogger(__name__)


def _base64url_encode(data: bytes) -> str:
    """Encode bytes as RFC 4648 base64url (URL-safe alphabet, no padding)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sha512_content_hash(data: bytes) -> str:
    """Return spec-compliant content_hash: `sha512-<base64url of digest>`."""
    return "sha512-" + _base64url_encode(hashlib.sha512(data).digest())


def _strip_empty_dict_placeholders(d: dict, keys: tuple = ("meta", "metadata")) -> None:
    """Drop keys whose value is an empty dict (vcon-lib Dialog.to_dict emits these unset)."""
    for k in keys:
        if k in d and d[k] == {}:
            del d[k]


# MIME type mapping for recording formats
MIME_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
}


class BaseRecordingData(ABC):
    """Abstract base class for platform-specific recording data.

    Subclasses must implement the abstract properties to normalize
    platform-specific data into a common format.
    """

    @property
    @abstractmethod
    def recording_id(self) -> str:
        """Platform-specific recording identifier."""
        pass

    @property
    @abstractmethod
    def from_number(self) -> str:
        """Caller's phone number in E.164 format."""
        pass

    @property
    @abstractmethod
    def to_number(self) -> str:
        """Callee's phone number in E.164 format."""
        pass

    @property
    @abstractmethod
    def direction(self) -> str:
        """Call direction (inbound, outbound, etc.)."""
        pass

    @property
    @abstractmethod
    def recording_url(self) -> str | None:
        """URL to download the recording, if available."""
        pass

    @property
    @abstractmethod
    def duration_seconds(self) -> float | None:
        """Recording duration in seconds."""
        pass

    @property
    @abstractmethod
    def start_time(self) -> datetime:
        """Recording start time as datetime."""
        pass

    @property
    def platform_tags(self) -> dict[str, str]:
        """Platform-specific tags to add to the vCon.

        Override in subclasses to add platform-specific metadata.

        Returns:
            Dictionary of tag name -> tag value
        """
        return {}


class BaseVconBuilder(ABC):
    """Base class for building vCons from recording data.

    Subclasses implement platform-specific download logic.
    """

    # Override in subclasses to identify the adapter
    ADAPTER_SOURCE = "generic_adapter"

    def __init__(self, download_recordings: bool = True, recording_format: str = "wav"):
        """Initialize builder.

        Args:
            download_recordings: Whether to download and embed recording audio
            recording_format: Preferred format for recordings (wav or mp3)
        """
        self.download_recordings = download_recordings
        self.recording_format = recording_format

    @abstractmethod
    def _download_recording(self, recording_data: BaseRecordingData) -> bytes | None:
        """Download recording audio from the platform.

        Args:
            recording_data: Recording data with URL and auth info

        Returns:
            Raw audio bytes or None if download fails
        """
        pass

    def _determine_originator(self, direction: str) -> int:
        """Determine which party originated the call.

        Args:
            direction: Call direction string

        Returns:
            Party index (0 = caller/from, 1 = callee/to)
        """
        # outbound = we called them, so party 0 initiated
        # inbound = they called us, so party 1 initiated
        if direction.lower() in ("outbound", "outbound-api", "outgoing"):
            return 0
        return 1

    def build(self, recording_data: BaseRecordingData) -> Vcon | None:
        """Build a vCon from recording data.

        Args:
            recording_data: Platform-specific recording data

        Returns:
            Vcon object or None if building fails
        """
        try:
            # Create vCon
            vcon = Vcon.build_new()

            # Set creation time
            start_time = recording_data.start_time
            try:
                vcon.created_at = start_time.isoformat()
            except AttributeError:
                logger.debug("Could not set created_at attribute")

            # Add parties (caller and callee)
            caller_party = Party(tel=recording_data.from_number)
            callee_party = Party(tel=recording_data.to_number)
            vcon.add_party(caller_party)
            vcon.add_party(callee_party)

            # Determine who initiated based on direction
            originator = self._determine_originator(recording_data.direction)

            # Build dialog. Spec field name is `mediatype` (NOT `mimetype`); the lib's
            # Dialog.__init__ takes `**kwargs` so the wrong name slides through silently.
            mime_type = MIME_TYPES.get(self.recording_format, "audio/wav")
            dialog_kwargs: dict[str, Any] = {
                "type": "recording",
                "start": start_time,
                "parties": [0, 1],
                "originator": originator,
                "mediatype": mime_type,
            }

            # Add duration if available
            if recording_data.duration_seconds is not None:
                dialog_kwargs["duration"] = recording_data.duration_seconds

            # Download and embed recording if configured. Spec encoding is `base64url`
            # (RFC 4648 URL-safe alphabet, no padding) — NOT plain `base64`.
            # Spec also requires `content_hash` whenever external media is referenced by URL.
            if self.download_recordings and recording_data.recording_url:
                audio_data = self._download_recording(recording_data)
                if audio_data:
                    dialog_kwargs["body"] = _base64url_encode(audio_data)
                    dialog_kwargs["encoding"] = "base64url"
                    dialog_kwargs["content_hash"] = _sha512_content_hash(audio_data)
                    dialog_kwargs["filename"] = (
                        f"{recording_data.recording_id}.{self.recording_format}"
                    )
                else:
                    # Fall back to URL reference if download fails. We have no audio
                    # bytes to hash, so the resulting dialog is not fully spec-compliant
                    # for external media (which requires url + content_hash).
                    logger.warning(
                        f"Download failed, emitting URL without content_hash for "
                        f"{recording_data.recording_id} — output is not spec-compliant "
                        f"for external media (spec requires url + content_hash)."
                    )
                    dialog_kwargs["url"] = f"{recording_data.recording_url}.{self.recording_format}"
            elif recording_data.recording_url:
                # URL-only mode (download_recordings=False) — same caveat as above.
                logger.warning(
                    "URL-only mode emits external media without content_hash; "
                    "output is not spec-compliant for external media."
                )
                dialog_kwargs["url"] = f"{recording_data.recording_url}.{self.recording_format}"

            # Create dialog
            dialog = Dialog(**dialog_kwargs)
            vcon.add_dialog(dialog)

            # vcon-lib Dialog.to_dict() emits empty `metadata: {}` and `meta: {}`
            # placeholders even when unset. Spec discourages empty placeholders.
            _strip_empty_dict_placeholders(vcon.vcon_dict["dialog"][-1])

            # Add source tag
            vcon.add_tag("source", self.ADAPTER_SOURCE)

            # Add platform-specific tags
            for tag_name, tag_value in recording_data.platform_tags.items():
                vcon.add_tag(tag_name, tag_value)

            logger.info(
                f"Created vCon {vcon.uuid} from recording {recording_data.recording_id} "
                f"(from: {recording_data.from_number}, to: {recording_data.to_number})"
            )

            return vcon

        except Exception as e:
            logger.error(f"Error building vCon from recording {recording_data.recording_id}: {e}")
            return None
