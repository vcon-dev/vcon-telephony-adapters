"""vCon builder to create vCons from Twilio recordings."""

import base64
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import requests
from vcon import Vcon
from vcon.party import Party
from vcon.dialog import Dialog


logger = logging.getLogger(__name__)


# MIME type mapping for Twilio recording formats
MIME_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
}


class TwilioRecordingData:
    """Data class to hold Twilio recording webhook data."""

    def __init__(self, webhook_data: Dict[str, Any]):
        """Initialize from Twilio webhook payload.

        Args:
            webhook_data: Dictionary of Twilio webhook parameters
        """
        # Core identifiers
        self.recording_sid = webhook_data.get("RecordingSid", "")
        self.account_sid = webhook_data.get("AccountSid", "")
        self.call_sid = webhook_data.get("CallSid", "")

        # Recording details
        self.recording_url = webhook_data.get("RecordingUrl", "")
        self.recording_status = webhook_data.get("RecordingStatus", "")
        self.recording_duration = webhook_data.get("RecordingDuration")
        self.recording_channels = webhook_data.get("RecordingChannels", "1")
        self.recording_source = webhook_data.get("RecordingSource", "")
        self.recording_start_time = webhook_data.get("RecordingStartTime")

        # Call participants
        self.from_number = webhook_data.get("From", "")
        self.to_number = webhook_data.get("To", "")
        self.caller = webhook_data.get("Caller", self.from_number)
        self.called = webhook_data.get("Called", self.to_number)

        # Direction and status
        self.direction = webhook_data.get("Direction", "")
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

    @property
    def duration_seconds(self) -> Optional[float]:
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


class VconBuilder:
    """Builds vCon objects from Twilio recording data."""

    def __init__(
        self,
        download_recordings: bool = True,
        recording_format: str = "wav",
        twilio_auth: Optional[tuple] = None
    ):
        """Initialize builder.

        Args:
            download_recordings: Whether to download and embed recording audio
            recording_format: Preferred format for recordings (wav or mp3)
            twilio_auth: Tuple of (account_sid, auth_token) for Twilio API
        """
        self.download_recordings = download_recordings
        self.recording_format = recording_format
        self.twilio_auth = twilio_auth

    def _download_recording(self, recording_url: str) -> Optional[bytes]:
        """Download recording audio from Twilio.

        Args:
            recording_url: Base URL to the recording

        Returns:
            Raw audio bytes or None if download fails
        """
        # Append format extension to get the audio file
        url = f"{recording_url}.{self.recording_format}"

        try:
            response = requests.get(
                url,
                auth=self.twilio_auth,
                timeout=60
            )

            if response.status_code == 200:
                logger.debug(f"Downloaded recording: {len(response.content)} bytes")
                return response.content
            else:
                logger.error(
                    f"Failed to download recording from {url}: "
                    f"status {response.status_code}"
                )
                return None

        except Exception as e:
            logger.error(f"Error downloading recording from {url}: {e}")
            return None

    def build(self, recording_data: TwilioRecordingData) -> Optional[Vcon]:
        """Build a vCon from Twilio recording data.

        Args:
            recording_data: TwilioRecordingData object with webhook payload

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
            # inbound = external party called in, outbound = we called out
            originator = 0 if recording_data.direction == "outbound" else 1

            # Build dialog
            mime_type = MIME_TYPES.get(self.recording_format, "audio/wav")
            dialog_kwargs = {
                "type": "recording",
                "start": start_time,
                "parties": [0, 1],
                "originator": originator,
                "mimetype": mime_type,
            }

            # Add duration if available
            if recording_data.duration_seconds is not None:
                dialog_kwargs["duration"] = recording_data.duration_seconds

            # Download and embed recording if configured
            if self.download_recordings and recording_data.recording_url:
                audio_data = self._download_recording(recording_data.recording_url)
                if audio_data:
                    audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                    dialog_kwargs["body"] = audio_base64
                    dialog_kwargs["encoding"] = "base64"
                    dialog_kwargs["filename"] = (
                        f"{recording_data.recording_sid}.{self.recording_format}"
                    )
                else:
                    # Fall back to URL reference if download fails
                    logger.warning(
                        f"Download failed, using URL reference for {recording_data.recording_sid}"
                    )
                    dialog_kwargs["url"] = (
                        f"{recording_data.recording_url}.{self.recording_format}"
                    )
            elif recording_data.recording_url:
                # Use URL reference without downloading
                dialog_kwargs["url"] = (
                    f"{recording_data.recording_url}.{self.recording_format}"
                )

            # Create dialog
            dialog = Dialog(**dialog_kwargs)
            vcon.add_dialog(dialog)

            # Add metadata tags
            vcon.add_tag("source", "twilio_adapter")
            vcon.add_tag("recording_sid", recording_data.recording_sid)
            vcon.add_tag("call_sid", recording_data.call_sid)
            vcon.add_tag("account_sid", recording_data.account_sid)

            if recording_data.direction:
                vcon.add_tag("direction", recording_data.direction)

            if recording_data.recording_source:
                vcon.add_tag("recording_source", recording_data.recording_source)

            if recording_data.duration_seconds is not None:
                vcon.add_tag(
                    "duration_seconds",
                    f"{recording_data.duration_seconds:.2f}"
                )

            # Add geographic metadata if available
            if recording_data.caller_city:
                vcon.add_tag("caller_city", recording_data.caller_city)
            if recording_data.caller_state:
                vcon.add_tag("caller_state", recording_data.caller_state)
            if recording_data.caller_country:
                vcon.add_tag("caller_country", recording_data.caller_country)
            if recording_data.called_city:
                vcon.add_tag("called_city", recording_data.called_city)
            if recording_data.called_state:
                vcon.add_tag("called_state", recording_data.called_state)
            if recording_data.called_country:
                vcon.add_tag("called_country", recording_data.called_country)

            logger.info(
                f"Created vCon {vcon.uuid} from Twilio recording {recording_data.recording_sid} "
                f"(from: {recording_data.from_number}, to: {recording_data.to_number})"
            )

            return vcon

        except Exception as e:
            logger.error(
                f"Error building vCon from recording {recording_data.recording_sid}: {e}"
            )
            return None
