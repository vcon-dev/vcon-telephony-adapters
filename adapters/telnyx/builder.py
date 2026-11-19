"""vCon builder for Telnyx recordings."""

import json
import logging
import math
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from vcon import Vcon
from vcon.dialog import Dialog
from vcon.event import Event
from vcon.party import Party
from vcon.session import Session

from core.base_builder import MIME_TYPES, BaseRecordingData, BaseVconBuilder
from core.lawful_basis import LawfulBasisConfig, RelayedBasisError, RelayedLawfulBasis
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

# The annotation names of the tenant's basis fields (`Annotation-basis_<key>`), the whole space.
BASIS_ANNOTATION = "basis_"

# The annotation that carries the tenant's add-on choice (`Annotation-addons`), and the add-on
# ids this adapter knows. The receiver's `ADDONS` is the same list; an id outside it is dropped.
ADDONS_ANNOTATION = "addons"
KNOWN_ADDONS = ("conv-triage", "voice-screen")
_ADDON_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

# The annotation that carries the call's screening outcome (`Annotation-screen_result`): one
# JSON object of at most `SCREEN_RESULT_LIMIT` bytes, the receiver's own cap. It is read
# into a `voice-screen` analysis entry (`relayed_screen_result`) and is never a tag.
SCREEN_RESULT_ANNOTATION = "screen_result"
# The receiver reserves every `Annotation-screen_` name and strips it from a carrier's body, so
# none of them is kept as a tag here: a look-alike name would hold whatever a sender wrote.
SCREEN_ANNOTATION_PREFIX = "screen_"
SCREEN_RESULT_LIMIT = 1024
SCREEN_RESULT_KEYS = (
    "status",
    "mode",
    "threshold",
    "windows",
    "frames",
    "synthetic_frames",
    "max_confidence",
    "decided_at",
)
SCREEN_ACTION_KEYS = ("action", "action_result", "acted_at", "telnyx_status")
_SCREEN_STATUSES = ("clean", "acted", "error", "refused")
_SCREEN_MODES = ("listen", "prompt_first")
_SCREEN_ACTIONS = ("bot", "announce", "stop")
_SCREEN_ACTION_RESULTS = ("accepted", "gone", "refused", "bot_unavailable")
_INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})"
)
SCREEN_ANALYSIS = {
    "type": "voice-screen",
    "vendor": "modulate",
    "product": "velma-2-synthetic-voice-detection-streaming",
    "schema": "voice-screen-result-v1",
}


def tenant_attachment(
    tenant_id: str, start: str, addons: Sequence[str] | None = None, session: int = 0
) -> dict[str, Any]:
    """The attachment that names the tenant a call belongs to.

    Draft-ietf-vcon-vcon-core-05 Attachment Object: `purpose`, not the legacy `type`; `start`,
    `party` and `session` present (the tenant owns the whole call, so the scope is the call's
    Session, as for every other attachment here); and with `encoding: "json"` the body is the JSON value itself,
    not a `json.dumps` string. A store reads the tenant from `body.id`. A string body under
    `encoding: "none"` would read back as no tenant at all.

    `addons` is the tenant's add-on choice, present only when a trusted relay sent one (an empty
    list means it sent one and nothing is on). `None` leaves the body as `{"id": ...}`.
    """
    body: dict[str, Any] = {"id": tenant_id}
    if addons is not None:
        body["addons"] = list(addons)
    return {
        "purpose": "tenant",
        "start": start,
        "party": 0,
        "session": session,
        "mediatype": "application/json",
        "encoding": "json",
        "body": body,
    }


def relayed_tenant(recording_data: Any) -> tuple[str | None, str | None]:
    """The call's tenant as a trusted relay named it: (tenant id, None), else (None, problem).

    A tenant counts only when the route trusted the request as relayed, the form names one
    tenant, and that value is a tenant id. `problem` is None when there is nothing to say (no
    field, or an untrusted request, which the route already logged), else the reason, naming
    only the value's type, never the value. The tenant attachment and the tenant's lawful
    basis both decide by this one rule.
    """
    values = getattr(recording_data, "relayed_tenants", ())
    if not values or getattr(recording_data, "relay_trusted", False) is not True:
        return None, None
    if len(set(values)) > 1:
        return None, "the form names more than one tenant, so no tenant attachment"
    value = values[-1]
    if not (isinstance(value, str) and TENANT_ID.fullmatch(value)):
        return None, (
            f"annotation_tenant is not a tenant id ({type(value).__name__}), "
            "so no tenant attachment"
        )
    return value, None


def relayed_addons(recording_data: Any) -> tuple[list[str] | None, str | None]:
    """The add-ons a trusted relay named: (ids, None), or (None, None) when it sent none.

    Only `Annotation-addons` from a request the route trusted as relayed counts, and the
    caller attaches it only beside a tenant (`relayed_tenant`). The value is comma separated
    ids: sorted and unique, with an id this adapter does not know dropped. A value that does
    not parse (not text, an empty or malformed id, or two different values on the form) gives
    `[]` and a `problem` naming only the reason, never the value. An empty value is `[]` with
    nothing to say.
    """
    values = getattr(recording_data, "relayed_addons", ())
    if not values or getattr(recording_data, "relay_trusted", False) is not True:
        return None, None
    if len({repr(v) for v in values}) > 1:
        return [], "annotation_addons names more than one list, so no add-ons"
    value = values[-1]
    if not isinstance(value, str):
        return [], f"annotation_addons is not text ({type(value).__name__}), so no add-ons"
    if value == "":
        return [], None
    ids = value.split(",")
    if not all(_ADDON_ID.fullmatch(i) for i in ids):
        return [], "annotation_addons does not parse as a comma separated list, so no add-ons"
    return sorted({i for i in ids if i in KNOWN_ADDONS}), None


def _one_of(value: Any, options: tuple[str, ...]) -> bool:
    return isinstance(value, str) and value in options


def _whole(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _real(value: Any, low: float, high: float) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _instant(value: Any) -> bool:
    """An ISO 8601 time with a zone, in the one shape the receiver writes."""
    if not isinstance(value, str) or not _INSTANT.fullmatch(value):
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _nullable(check, value: Any, *args: Any) -> bool:
    return value is None or check(value, *args)


def _object_without_repeats(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("repeated key")
    return dict(pairs)


def _refuse_constant(name: str) -> Any:
    raise ValueError("not a JSON number")


def _acted_agree(doc: dict[str, Any]) -> bool:
    """`acted_at` and `telnyx_status` are non-null exactly when the receiver acted on the call."""
    acted = doc["action_result"] in ("accepted", "gone")
    return (doc["acted_at"] is not None) == acted and (doc["telnyx_status"] is not None) == acted


def screen_result_body(value: Any) -> dict[str, Any] | None:
    """The `voice-screen` analysis body for one `Annotation-screen_result` value, or None.

    The value is JSON text (the receiver sends it compact, but spacing and key order are not
    checked) of at most `SCREEN_RESULT_LIMIT` bytes: one object with no repeated key and no NaN
    or Infinity, holding exactly the contract's keys.
    Those are the eight result keys and the four action keys, or `{"status": "missing"}` alone
    or with the four action keys. Every value is checked for its type and range: the status,
    mode, action and action result against their lists, counts as whole numbers in range, the
    threshold and confidence from 0 to 1, times as ISO 8601 with a zone, the carrier status as
    a whole HTTP status or null. The action fields must agree: `acted_at` and `telnyx_status` are
    set exactly when `action_result` is `accepted` or `gone` (the receiver redirected the call, or
    found it already ended), and null otherwise. The body returned is rebuilt from those checked values, in the
    contract's order, so no text from the request is copied: an enum matched a fixed string and
    a time matched a fixed shape. Anything else gives None and writes nothing.
    """
    if not isinstance(value, str) or len(value.encode("utf-8", "replace")) > SCREEN_RESULT_LIMIT:
        return None
    # `parse_constant` here and `isfinite` in `_real` are deliberately redundant with the range
    # checks (a NaN or an infinity is out of range anyway): they stay so that widening a range
    # cannot let one through. The same goes for the byte count: no value that passes the checks
    # holds a non-ASCII character, so bytes and characters agree.
    try:
        doc = json.loads(
            value, object_pairs_hook=_object_without_repeats, parse_constant=_refuse_constant
        )
    except (ValueError, RecursionError):
        return None
    if not isinstance(doc, dict):
        return None
    missing = doc.get("status") == "missing"
    full = {*SCREEN_RESULT_KEYS, *SCREEN_ACTION_KEYS}
    with_action = {"status", *SCREEN_ACTION_KEYS}
    if set(doc) not in ((full,) if not missing else ({"status"}, with_action)):
        return None
    action_keys = [k for k in SCREEN_ACTION_KEYS if k in doc]
    checks = {
        "action": lambda v: _nullable(_one_of, v, _SCREEN_ACTIONS),
        "action_result": lambda v: _nullable(_one_of, v, _SCREEN_ACTION_RESULTS),
        "acted_at": lambda v: _nullable(_instant, v),
        "telnyx_status": lambda v: _nullable(_whole, v, 100, 599),
    }
    if not all(checks[k](doc[k]) for k in action_keys):
        return None
    if action_keys and not _acted_agree(doc):
        return None
    if missing:
        return {k: doc[k] for k in ("status", *action_keys)}
    result_checks = {
        "status": lambda v: _one_of(v, _SCREEN_STATUSES),
        "mode": lambda v: _one_of(v, _SCREEN_MODES),
        "threshold": lambda v: _real(v, 0, 1),
        "windows": lambda v: _whole(v, 1, 4),
        "frames": lambda v: _whole(v, 0, 3600),
        "synthetic_frames": lambda v: _whole(v, 0, 3600),
        "max_confidence": lambda v: _nullable(_real, v, 0, 1),
        "decided_at": lambda v: _nullable(_instant, v),
    }
    if not all(result_checks[k](doc[k]) for k in SCREEN_RESULT_KEYS):
        return None
    return {k: doc[k] for k in (*SCREEN_RESULT_KEYS, *SCREEN_ACTION_KEYS)}


def relayed_screen_result(recording_data: Any) -> tuple[dict[str, Any] | None, str | None]:
    """The screening outcome a trusted relay sent: (body, None), or (None, reason).

    `(None, None)` means nothing to do: no field, or a request the route did not trust. The
    caller uses it only beside a trusted tenant. The same value twice counts once; two
    different values, a value that is not text, and a value `screen_result_body` refuses give
    no body and a reason that names neither the value nor any part of it.
    """
    values = getattr(recording_data, "relayed_screen_results", ())
    if not values or getattr(recording_data, "relay_trusted", False) is not True:
        return None, None
    if len({repr(v) for v in values}) > 1:
        return None, "annotation_screen_result names more than one value, so it is dropped"
    value = values[-1]
    if not isinstance(value, str):
        return None, (
            f"annotation_screen_result is not text ({type(value).__name__}), so it is dropped"
        )
    body = screen_result_body(value)
    if body is None:
        return None, "annotation_screen_result is not the screening contract, so it is dropped"
    return body, None


def _is_telnyx_host(url: str) -> bool:
    """True only for Telnyx's own API host.

    Recording URLs point at pre-signed S3, which must be fetched anonymously.
    """
    try:
        return urlparse(url).hostname in TELNYX_API_HOSTS
    except ValueError:
        return False


def _seconds_between(start: str, end: str) -> float | None:
    """Seconds from `start` to `end` (ISO 8601), or None if either is missing or bad."""
    if not (start and end):
        return None
    try:
        t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    seconds = round((t1 - t0).total_seconds(), 3)
    return seconds if seconds >= 0 else None


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
        relayed_basis: dict[str, Sequence[Any]] | None = None,
        relayed_addons: Sequence[Any] = (),
        relayed_screen_results: Sequence[Any] = (),
    ):
        """Initialize from Telnyx webhook event data.

        Args:
            event_data: the raw webhook event
            session: accumulated lifecycle facts for this call, if any
            recording_meta: the recordings-API resource, if it was fetched
            relayed_tenants: every `Annotation-tenant` value on a relayed form, in order
            relay_trusted: True only when the route trusted the request as coming from the
                relay. Arguments, not event fields, so no request body can set them.
            relayed_basis: every `Annotation-basis_<key>` value on a relayed form, by key,
                in order. Read only beside a trusted tenant.
            relayed_addons: every `Annotation-addons` value on a relayed form, in order. Read
                only from a trusted relay and only beside a trusted tenant.
            relayed_screen_results: every `Annotation-screen_result` value on a relayed form, in
                order. Read only from a trusted relay and only beside a trusted tenant.

        The recording event alone cannot name the parties. Either supplement
        supplies `from`/`to`; without one, the vCon is anonymous and the
        builder says so.
        """
        self._session = session
        self._recording_meta = recording_meta or {}
        self.relayed_tenants = tuple(relayed_tenants)
        self.relay_trusted = relay_trusted is True
        self.relayed_basis = {k: tuple(v) for k, v in (relayed_basis or {}).items()}
        self.relayed_addons = tuple(relayed_addons)
        self.relayed_screen_results = tuple(relayed_screen_results)
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

        # With a tenant that counts, the basis fields become the tenant's lawful_basis attachment
        # (or are refused), so they are not also kept as tags: the justification and names would
        # be in the vCon twice. Without one, every annotation stays a tag, as before. The add-on
        # list is never a tag, with a tenant or without: it is read from the tenant attachment
        # alone, and a tag is a copy anyone could have written.
        tenant, _ = relayed_tenant(self)
        for name, value in (self._payload.get("annotations") or {}).items():
            if tenant is not None and name.startswith(BASIS_ANNOTATION):
                continue
            if name.lower() == ADDONS_ANNOTATION or name.lower().startswith(
                SCREEN_ANNOTATION_PREFIX
            ):
                continue
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
        """Build a core-05 vCon (syntax 0.5.0) from a recording and its call.

        One Session per call: the receivers are on the Session, the contributors
        on the recording Dialog. Lifecycle facts become party Events, a SIP
        REFER becomes transfer Events, and every attachment carries its scope.
        """
        try:
            return self._build_core_05(recording_data)
        except Exception as exc:  # noqa: BLE001 - one bad call must not kill the webhook
            logger.error(
                "Error building vCon from recording %s: %s", recording_data.recording_id, exc
            )
            return None

    def _build_core_05(self, recording_data: BaseRecordingData):
        call = getattr(recording_data, "_session", None)
        start = recording_data.start_time
        vcon = Vcon.build_new()

        # Party 0 is the Telnyx `from`, party 1 the `to`. Every party is a
        # receiver of the call, so both sit on the Session.
        vcon.add_party(Party(tel=recording_data.from_number))
        vcon.add_party(Party(tel=recording_data.to_number))
        inbound = recording_data.direction == "inbound"

        call_start = (call.start_time if call and call.start_time else None) or start.isoformat()
        call_duration = _seconds_between(call_start, call.end_time) if call else None
        if call_duration is None:
            call_duration = recording_data.duration_seconds

        # `originator` only when it is not the first party. This keeps the
        # mapping of the 0.4.0 adapter (an incoming call is originated by
        # party 1); see CHANGELOG.
        originator = self._determine_originator(recording_data.direction)
        session = Session(
            start=call_start,
            duration=call_duration,
            parties=[0, 1],
            originator=originator if originator != 0 else None,
            session_id=(
                {"local": recording_data.call_session_id, "remote": recording_data.call_session_id}
                if recording_data.call_session_id
                else None
            ),
        )
        session_index = vcon.add_session(session)

        dialog_kwargs: dict[str, Any] = {
            "type": "recording",
            "start": start,
            # Contributors to the recording. Dual-channel or mono, both speak.
            "parties": [0, 1],
            "mediatype": MIME_TYPES.get(self.recording_format, "audio/wav"),
        }
        if recording_data.duration_seconds is not None:
            dialog_kwargs["duration"] = recording_data.duration_seconds
        filename = f"{recording_data.recording_id}.{self.recording_format}"
        self._add_media(dialog_kwargs, recording_data, filename)
        vcon.add_dialog(Dialog(**dialog_kwargs), session=session_index)

        if call is not None:
            self._add_party_events(vcon, call, session_index, call_start)
            self._add_transfer_events(vcon, call, session_index, inbound)

        vcon.add_tag("source", self.ADAPTER_SOURCE)
        for tag_name, tag_value in recording_data.platform_tags.items():
            vcon.add_tag(tag_name, tag_value)

        # The tenant's relayed basis, else the deployment's; never invented.
        # Scoped with party plus session (core-05).
        self._apply_lawful_basis(vcon, recording_data, session_index=session_index)

        if call is not None:
            declare_extension(vcon.vcon_dict, SIP_SIGNALING_EXTENSION)
            vcon.vcon_dict.setdefault("attachments", []).append(
                {
                    "purpose": "sip-message-trace",
                    "start": vcon.vcon_dict["created_at"],
                    "party": 0,
                    "session": session_index,
                    "mediatype": "application/json",
                    "encoding": "json",
                    "body": json.dumps(sip_signaling_attachment(call)),
                }
            )

        self._attach_tenant(vcon, recording_data, session_index)
        self._attach_screen_result(vcon, recording_data)

        # Attachments the library built already carry party and scope; only
        # `start` and `mediatype` are backfilled.
        for attachment in vcon.vcon_dict.get("attachments", []):
            attachment.setdefault("start", vcon.vcon_dict["created_at"])
            if attachment.get("body") is not None:
                attachment.setdefault("mediatype", "application/json")

        if not recording_data.from_number or not recording_data.to_number:
            logger.warning(
                "vCon %s has no party identity. `call.recording.saved` omits "
                "from/to; supply a CallSession or let the recordings API be "
                "consulted.",
                vcon.uuid,
            )
        logger.info(
            "Created vCon %s from recording %s (from: %s, to: %s)",
            vcon.uuid,
            recording_data.recording_id,
            recording_data.from_number,
            recording_data.to_number,
        )
        return vcon

    def _attach_tenant(
        self, vcon, recording_data: BaseRecordingData, session_index: int = 0
    ) -> None:
        """Add the tenant attachment when a trusted relay named the call's tenant.

        The source is the `Annotation-tenant` form field, which also stays an
        `annotation_tenant` tag as before. Nothing is attached unless the route trusted the
        request as relayed (it logs why not), the form names one tenant, and that value is a
        tenant id. Logs name only the reason or the value's type, never the value. With no
        such field the vCon is left exactly as it was. `start` is `created_at`, the same as
        every other attachment here.

        The body also carries `addons` when the same trusted relay sent `Annotation-addons`
        (`relayed_addons`), and only then: with no field the body is `{"id": ...}` as before.
        With no tenant there is no attachment, so an add-on list never travels without one.
        """
        tenant, problem = relayed_tenant(recording_data)
        if problem:
            logger.warning("vCon %s: %s", vcon.uuid, problem)
        if tenant is None:
            return
        addons, addons_problem = relayed_addons(recording_data)
        if addons_problem:
            logger.warning("vCon %s: %s", vcon.uuid, addons_problem)
        vcon.vcon_dict.setdefault("attachments", []).append(
            tenant_attachment(tenant, vcon.created_at, addons, session=session_index)
        )

    def _attach_screen_result(self, vcon, recording_data: BaseRecordingData) -> None:
        """Add the `voice-screen` analysis entry when a trusted relay sent a valid outcome.

        The source is the `Annotation-screen_result` form field, which is never a tag. It counts
        only from a request the route trusted as relayed, with the setting on (the route
        drops it otherwise), beside a tenant that counts (`relayed_tenant`), and only when
        `screen_result_body` accepts it. One entry, on dialog 0, the body rebuilt from checked
        values. Anything else writes nothing, and the log names only the reason. With no such
        field the vCon is left exactly as it was.
        """
        if not getattr(recording_data, "relayed_screen_results", ()):
            return
        tenant, _ = relayed_tenant(recording_data)
        body, problem = relayed_screen_result(recording_data)
        if problem:
            logger.warning("vCon %s: %s", vcon.uuid, problem)
        if body is None:
            return
        if tenant is None:
            logger.warning(
                "vCon %s: annotation_screen_result came with no usable tenant, so it is dropped",
                vcon.uuid,
            )
            return
        vcon.add_analysis(dialog=0, encoding="json", body=body, **SCREEN_ANALYSIS)

    def _apply_lawful_basis(
        self, vcon, recording_data: BaseRecordingData, session_index: int = 0
    ) -> None:
        """The call's tenant's own basis when a trusted relay names the tenant, else as before.

        With no tenant (no field, an untrusted request, two tenants, or a value that is not a
        tenant id) the deployment's global basis applies exactly as it always has, and any
        basis fields on the form are ignored. With a tenant, the global basis is never used:
        the attachment is built from the relayed `Annotation-basis_<key>` fields alone
        (`RelayedLawfulBasis`), with the deployment's purposes. No basis fields, or fields that
        are invalid or partial, mean no lawful_basis attachment at all and one line naming
        only the reason. With a tenant the basis fields are not also kept as tags
        (`TelnyxRecordingData.platform_tags`).
        """
        tenant, _ = relayed_tenant(recording_data)
        relayed = getattr(recording_data, "relayed_basis", None) or {}
        if tenant is None:
            if relayed and getattr(recording_data, "relay_trusted", False) is True:
                logger.warning(
                    "vCon %s: basis fields came with no usable tenant, so they are ignored",
                    vcon.uuid,
                )
            super()._apply_lawful_basis(vcon, recording_data, session_index=session_index)
            return

        if not relayed:
            logger.warning(
                "vCon %s: the relay sent no lawful basis for the call's tenant, so no "
                "lawful_basis attachment; the global basis is never used for a tenant",
                vcon.uuid,
            )
            return
        purposes = self.lawful_basis.purposes if self.lawful_basis is not None else ()
        try:
            basis = RelayedLawfulBasis.from_fields(relayed, purposes)
        except RelayedBasisError as refused:
            logger.warning(
                "vCon %s: the tenant's relayed lawful basis is refused (%s), so no "
                "lawful_basis attachment",
                vcon.uuid,
                refused.reason,
            )
            return
        basis.apply(vcon, session_index=session_index)

    @staticmethod
    def _add_party_events(vcon: Vcon, call: CallSession, session_index: int, call_start: str):
        """join on initiate and answer, drop for both parties at hangup.

        Telnyx sends no hold webhook, so no hold/unhold Events are produced.
        """
        if call.start_time:
            vcon.add_event(
                Event(type="join", time=call.start_time, sessions=session_index, party=0)
            )
        if call.answered_at:
            vcon.add_event(
                Event(type="join", time=call.answered_at, sessions=session_index, party=1)
            )
        if call.end_time:
            for party in (0, 1):
                vcon.add_event(
                    Event(type="drop", time=call.end_time, sessions=session_index, party=party)
                )

    @staticmethod
    def _add_transfer_events(vcon: Vcon, call: CallSession, session_index: int, inbound: bool):
        """One blind-transfer, or consultation-start plus consultative-transfer, per REFER.

        The transferor is our side of the call: the callee on an inbound call,
        the caller on an outbound one. The other original party is the transferee.
        The target call and any consultation call are placeholder Sessions.
        """
        transferor, transferee = (1, 0) if inbound else (0, 1)
        for transfer in call.transfers:
            time = transfer.get("time") or call.answered_at or call.start_time
            if not time:
                logger.warning("Transfer with no time on call %s; skipped", call.call_session_id)
                continue
            target = transfer.get("target") or ""
            vcon.add_party(Party(tel=target) if target else Party())
            target_party = len(vcon.vcon_dict["parties"]) - 1
            target_session = vcon.add_session(
                Session(start=time, parties=[transferee, target_party])
            )
            consultation_id = transfer.get("consultation_call_session_id")
            if consultation_id:
                consult_start = transfer.get("consultation_started_at") or time
                consultation = vcon.add_session(
                    Session(
                        start=consult_start,
                        parties=[transferor, target_party],
                        session_id={"local": consultation_id, "remote": consultation_id},
                    )
                )
                vcon.add_event(
                    Event(
                        type="consultation-start",
                        time=consult_start,
                        sessions=session_index,
                        party=transferor,
                        transferee=transferee,
                        transfer_target=target_party,
                        consultation=consultation,
                    )
                )
                vcon.add_event(
                    Event(
                        type="consultative-transfer",
                        time=time,
                        sessions=session_index,
                        party=transferor,
                        transferee=transferee,
                        transfer_target=target_party,
                        consultation=consultation,
                        target_session=target_session,
                    )
                )
            else:
                vcon.add_event(
                    Event(
                        type="blind-transfer",
                        time=time,
                        sessions=session_index,
                        party=transferor,
                        transferee=transferee,
                        transfer_target=target_party,
                        target_session=target_session,
                    )
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
