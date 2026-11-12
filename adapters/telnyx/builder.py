"""vCon builder for Telnyx recordings."""

import json
import logging
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from core.base_builder import BaseRecordingData, BaseVconBuilder
from core.lawful_basis import LawfulBasisConfig
from core.media_publisher import AudioPublisher

from .call_session import (
    SIP_SIGNALING_EXTENSION,
    CallSession,
    declare_extension,
    sip_signaling_attachment,
)

logger = logging.getLogger(__name__)

TELNYX_API_HOSTS = ("api.telnyx.com",)

# A tenant id: `t-` and 16 lowercase hex, the form the relaying receiver itself checks before
# it adds `Annotation-tenant`. Anything else is not a tenant.
TENANT_ID = re.compile(r"t-[0-9a-f]{16}")


def tenant_attachment(tenant_id: str, start: str) -> dict[str, Any]:
    """The attachment that names the tenant a call belongs to.

    Draft-ietf-vcon-vcon-core-04 Attachment Object: `purpose`, not the legacy `type`; `start`,
    `party` and `dialog` present; and with `encoding: "json"` the body is the JSON value itself,
    not a `json.dumps` string. A store reads the tenant from `body.id`. A string body under
    `encoding: "none"` would read back as no tenant at all.
    """
    return {
        "purpose": "tenant",
        "start": start,
        "party": 0,
        "dialog": 0,
        "mediatype": "application/json",
        "encoding": "json",
        "body": {"id": tenant_id},
    }


def _is_telnyx_host(url: str) -> bool:
    """True only for Telnyx's own API host.

    Recording URLs point at pre-signed S3, which must be fetched anonymously.
    """
    try:
        return urlparse(url).hostname in TELNYX_API_HOSTS
    except ValueError:
        return False


class TelnyxRecordingData(BaseRecordingData):
    """Recording data from Telnyx webhook event.

    Telnyx sends webhooks for call recording events with
    standardized event structure.

    Observed payload (a real `call.recording.saved`, 2026-09-09, CON-844):

    {
        "data": {
            "event_type": "call.recording.saved",
            "occurred_at": "...",
            "payload": {
                "call_control_id": "v3:...",
                "call_leg_id": "...",
                "call_session_id": "...",
                "connection_id": "...",
                "recording_id": "...",
                "recording_urls": {"wav": "https://s3.amazonaws.com/...?X-Amz-..."},
                "public_recording_urls": {},
                "channels": "dual",
                "format": "wav",
                "calling_party_type": "pstn",
                "flow_destination": "telnyx_number_cc_app",
                "recording_started_at": "...",
                "recording_ended_at": "...",
                "start_time": "...",
                "end_time": "..."
            }
        }
    }

    Note what is NOT there: `from`, `to`, `direction`, `duration_millis`. An
    earlier version of this docstring claimed all four. Party identity lives
    only on the lifecycle events (`call.initiated`, `.answered`, `.hangup`),
    so a recording event on its own cannot produce a vCon with parties. See
    CON-803: correlating those events is required, not an enhancement.
    """

    def __init__(
        self,
        event_data: dict[str, Any],
        session: "CallSession | None" = None,
        recording_meta: dict[str, Any] | None = None,
        relayed_tenants: Sequence[Any] = (),
        relay_trusted: bool = False,
    ):
        """Initialize from Telnyx webhook event data.

        Args:
            event_data: the raw webhook event
            session: accumulated lifecycle facts for this call, if any
            recording_meta: the recordings-API resource, if it was fetched
            relayed_tenants: every `Annotation-tenant` value on a relayed form, in order
            relay_trusted: True only when the route trusted the request as coming from the
                relay. Arguments, not event fields, so no request body can set them.

        The recording event alone cannot name the parties. Either supplement
        supplies `from`/`to`; without one, the vCon is anonymous and the
        builder says so.
        """
        self._session = session
        self._recording_meta = recording_meta or {}
        self.relayed_tenants = tuple(relayed_tenants)
        self.relay_trusted = relay_trusted is True
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
        return self._payload.get("recording_id", self._payload.get("call_control_id", ""))

    @property
    def call_session_id(self) -> str:
        """Telnyx call session ID."""
        return self._payload.get("call_session_id", "")

    def _enriched(self, key: str) -> str:
        """Value from the payload, else the lifecycle session, else the API."""
        for source in (
            self._payload,
            self._session.__dict__ if self._session else {},
            self._recording_meta,
        ):
            value = source.get(key) or source.get(f"{key}_number")
            if value:
                return str(value)
        return ""

    @property
    def from_number(self) -> str:
        """Caller's number. Absent from the recording event itself."""
        return self._enriched("from")

    @property
    def to_number(self) -> str:
        """Called number. Absent from the recording event itself."""
        return self._enriched("to")

    @property
    def direction(self) -> str:
        """Call direction. Absent from the recording event itself."""
        direction = self._enriched("direction") or "incoming"
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
    def recording_urls(self) -> dict[str, str]:
        """All available recording URLs by format."""
        return self._payload.get("recording_urls", {})

    @property
    def duration_seconds(self) -> float | None:
        """Recording duration in seconds.

        `duration_millis` is not actually sent on `call.recording.saved`; the
        real payload carries `recording_started_at` / `recording_ended_at`.
        Both are handled, timestamps first.
        """
        started = self._payload.get("recording_started_at")
        ended = self._payload.get("recording_ended_at")
        if started and ended:
            try:
                t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(ended.replace("Z", "+00:00"))
                return round((t1 - t0).total_seconds(), 3)
            except (ValueError, AttributeError, TypeError):
                pass

        duration_millis = self._payload.get("duration_millis") or self._recording_meta.get(
            "duration_millis"
        )
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
    def platform_tags(self) -> dict[str, str]:
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

        # TeXML only: when the call began and when recording began. The gap between
        # them is the ringback, where a pre-answer notice plays.
        for key in ("call_initiated_at", "recording_started_at"):
            if self._payload.get(key):
                tags[key] = self._payload[key]

        for name, value in (self._payload.get("annotations") or {}).items():
            tags[f"annotation_{name}"] = value

        return tags


class TelnyxVconBuilder(BaseVconBuilder):
    """Build vCons from Telnyx recording events."""

    ADAPTER_SOURCE = "telnyx_adapter"

    def __init__(
        self,
        download_recordings: bool = True,
        recording_format: str = "wav",
        api_key: str | None = None,
        publisher: AudioPublisher | None = None,
        lawful_basis: LawfulBasisConfig | None = None,
    ):
        """Initialize Telnyx vCon builder.

        Args:
            download_recordings: Whether to download and embed recordings
            recording_format: Preferred recording format (wav or mp3)
            api_key: Telnyx API key for authenticated downloads
        """
        super().__init__(download_recordings, recording_format, publisher, lawful_basis)
        self.api_key = api_key

    def build(self, recording_data: BaseRecordingData):
        """Build, then attach signalling detail from the lifecycle events and the call's tenant."""
        vcon = super().build(recording_data)
        if vcon is None:
            return None

        session = getattr(recording_data, "_session", None)
        if session is not None:
            declare_extension(vcon.vcon_dict, SIP_SIGNALING_EXTENSION)
            vcon.vcon_dict.setdefault("attachments", []).append(
                {
                    "purpose": "sip-message-trace",
                    "party": 0,
                    "dialog": 0,
                    "mediatype": "application/json",
                    "encoding": "json",
                    "body": json.dumps(sip_signaling_attachment(session)),
                }
            )

        self._attach_tenant(vcon, recording_data)

        if not recording_data.from_number or not recording_data.to_number:
            logger.warning(
                "vCon %s has no party identity. `call.recording.saved` omits "
                "from/to; supply a CallSession or let the recordings API be "
                "consulted.",
                vcon.uuid,
            )
        return vcon

    def _attach_tenant(self, vcon, recording_data: BaseRecordingData) -> None:
        """Add the tenant attachment when a trusted relay named the call's tenant.

        The source is the `Annotation-tenant` form field, which also stays an
        `annotation_tenant` tag as before. Nothing is attached unless the route trusted the
        request as relayed (it logs why not), the form names one tenant, and that value is a
        tenant id. Logs name only the reason or the value's type, never the value. With no
        such field the vCon is left exactly as it was. `start` is `created_at`, the same as
        every other attachment here.
        """
        values = getattr(recording_data, "relayed_tenants", ())
        if not values or getattr(recording_data, "relay_trusted", False) is not True:
            return
        if len(set(values)) > 1:
            logger.warning(
                "vCon %s: the form names more than one tenant, so no tenant attachment",
                vcon.uuid,
            )
            return
        value = values[-1]
        if not (isinstance(value, str) and TENANT_ID.fullmatch(value)):
            logger.warning(
                "vCon %s: annotation_tenant is not a tenant id (%s), so no tenant attachment",
                vcon.uuid,
                type(value).__name__,
            )
            return
        vcon.vcon_dict.setdefault("attachments", []).append(
            tenant_attachment(value, vcon.created_at)
        )

    def _reference_url(self, recording_url: str) -> str:
        """Telnyx URLs are already per-format and pre-signed.

        `recording_urls` is a dict keyed by format, and each value carries an
        AWS query string. Appending `.wav` would land after the signature and
        produce a link that cannot resolve.
        """
        return recording_url

    def _download_recording(self, recording_data: BaseRecordingData) -> bytes | None:
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

            # Telnyx hands back a **pre-signed S3 URL**, not a Telnyx-hosted one.
            # Two reasons never to attach the bearer token to it:
            #   1. S3 rejects a request carrying both a presigned signature and an
            #      Authorization header -> 400, so every download failed.
            #   2. Under BYOK that token is the *customer's* Telnyx API key, and
            #      sending it to s3.amazonaws.com leaks their credential to a
            #      third party.
            # Only authenticate to Telnyx's own API host.
            headers = {}
            if self.api_key and _is_telnyx_host(recording_url):
                headers["Authorization"] = f"Bearer {self.api_key}"

            response = requests.get(recording_url, headers=headers, timeout=60)
            response.raise_for_status()
            return response.content

        except requests.RequestException as e:
            logger.error(f"Failed to download Telnyx recording {telnyx_data.recording_id}: {e}")
            return None
