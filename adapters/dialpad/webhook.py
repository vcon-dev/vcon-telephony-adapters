"""FastAPI webhook receiver for Dialpad call events.

Dialpad call event subscriptions
(https://developers.dialpad.com/docs/call-events) POST the event as a compact
JWT signed HS256 with the subscription secret, or as plain JSON when the
subscription has no secret. Each call state is a separate event, and they
arrive out of order.

Lifecycle: one vCon per call, with one uuid for its whole life. The first
buildable event (`recording`, `call_transcription`, `voicemail_uploaded` or a
`recap_*` state) builds and posts it. The tracker record for the call keeps
the uuid, created_at and what was included (recording details, whether the
transcript made it, the recap components, the voicemail text). A later event
that could add something rebuilds the whole vCon under the same uuid, from
everything known so far, and posts it again; one that adds nothing answers
`already_processed`. No timers: everything is driven by events, and the
tracker file makes it restart safe.

Whether the JWT carries `iat`/`exp` is undocumented, so neither is required.
Replay protection uses the event's own `event_timestamp` (unix milliseconds),
checked only after the signature has authenticated the payload.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import jwt
from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from core.poster import HttpPoster
from core.tracker import StateTracker
from core.webhook_security import is_timestamp_fresh

from .builder import (
    BUILDABLE_STATES,
    RECAP_STATES,
    TRANSCRIPTION_STATE,
    VOICEMAIL_STATE,
    DialpadBuild,
    DialpadBuildError,
    DialpadVconBuilder,
    recording_key,
)
from .config import DialpadConfig

logger = logging.getLogger(__name__)

# Retry policy. A failed build or post (download, API, publish or conserver)
# answers 503 so Dialpad can redeliver; its retry policy is undocumented, and
# redeliveries are bounded by the replay window since they carry the original
# event_timestamp. Failures are counted per call and reset by a successful
# post. On the MAX_ATTEMPTS-th consecutive failure the adapter gives up: it
# posts whatever recordings did arrive (same uuid), records ABANDONED, which
# is final, and answers 200 so a broken call cannot hold Dialpad in a retry
# loop. Any later event for the call, not only a redelivery, retries a
# pending call or a failed update. Twilio and ElevenLabs instead ack 200 and
# record the failure as final; Dialpad events only reference the audio, so a
# transient API or download failure there would otherwise lose the call.
MAX_ATTEMPTS = 5

PENDING = "pending"  # nothing posted yet; retried by any later event
POSTED = "posted"  # posted at least once; later events may update it
ABANDONED = "abandoned"  # gave up; final, never rebuilt


class _KeyLocks:
    """One lock per call id, dropped once no thread holds or waits on it."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, list[Any]] = {}  # key -> [lock, holders + waiters]

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._guard:
            entry = self._locks.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._guard:
                entry[1] -= 1
                if entry[1] == 0:
                    del self._locks[key]

    def __len__(self) -> int:
        with self._guard:
            return len(self._locks)


_JWT_SHAPE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")


def _looks_like_jwt(text: str) -> bool:
    return bool(_JWT_SHAPE.match(text))


def create_app(config: DialpadConfig) -> FastAPI:
    """Create and configure the FastAPI application for the Dialpad adapter."""
    app = FastAPI(
        title="vCon Dialpad Adapter",
        description="Receives Dialpad call events and creates vCons",
        version="0.1.0",
    )

    publisher = config.build_publisher()
    if publisher is None:
        logger.warning(
            "MEDIA_BACKEND=embed: audio will be inlined as base64, making each "
            "vCon roughly 1.3x the size of the recording. Set MEDIA_BACKEND=s3 "
            "for anything beyond a lab."
        )
    lawful_basis = config.build_lawful_basis()
    if not lawful_basis.enabled:
        logger.warning(
            "LAWFUL_BASIS is unset, so vCons will carry no record of why this "
            "deployment may hold the recording. Fine for a lab; not for real "
            "conversations."
        )

    builder = DialpadVconBuilder(
        client=config.build_client(),
        publisher=publisher,
        lawful_basis=lawful_basis,
        include_transcript=config.include_transcript,
        include_recap=config.include_recap,
        include_admin_recordings=config.include_admin_recordings,
        download_recordings=config.download_recordings,
    )
    poster = HttpPoster(config.conserver_url, config.get_headers(), config.ingress_lists)
    tracker = StateTracker(config.state_file)

    # Handlers run in worker threads (the build does blocking I/O). StateTracker
    # mutates a plain dict and rewrites its file with no locking, so every
    # tracker call goes through one global lock, held only for the call. A
    # per-call lock is held across read-build-post-write of the call's record
    # so two events for one call never interleave, while different calls
    # proceed in parallel.
    tracker_lock = threading.Lock()
    call_locks = _KeyLocks()
    app.state.call_locks = call_locks

    def load_record(key: str) -> dict | None:
        with tracker_lock:
            entry = tracker.get_metadata(key)
            return copy.deepcopy(entry) if entry is not None else None

    def save_record(key: str, record: dict[str, Any]) -> None:
        fields = {k: v for k, v in record.items() if k not in ("vcon_uuid", "status", "timestamp")}
        with tracker_lock:
            tracker.mark_processed(
                key, record.get("vcon_uuid") or "", status=record["status"], **fields
            )

    def decode_payload(body: bytes) -> Any:
        """Authenticate and decode the request body into the event payload.

        Raises HTTPException: 401 for a bad signature or a stale event, 400
        for a body that cannot be decoded.
        """
        try:
            text = body.decode("utf-8").strip()
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Invalid body") from None

        # With a secret, a JWT-shaped body is always verified; validation off
        # only means unsigned plain JSON is accepted too.
        if config.webhook_secret and (config.validate_webhook or _looks_like_jwt(text)):
            try:
                # Pin the algorithm so `alg=none` and HS/RS confusion fail.
                payload = jwt.decode(
                    text,
                    config.webhook_secret,
                    algorithms=["HS256"],
                    options={"require": []},
                    # Allow some clock skew should Dialpad add iat/nbf/exp.
                    leeway=60,
                )
            except jwt.PyJWTError:
                logger.warning("Invalid Dialpad webhook signature")
                raise HTTPException(status_code=401, detail="Invalid signature") from None

            # The payload is authenticated now, so its timestamp can be trusted
            # for the replay check. Logged apart from a bad signature so an
            # operator can tell replay from tampering.
            timestamp = payload.get("event_timestamp") if isinstance(payload, dict) else None
            try:
                seconds = float(timestamp) / 1000 if timestamp is not None else None
            except (TypeError, ValueError):
                seconds = None
            if not is_timestamp_fresh(seconds, config.webhook_timestamp_tolerance_seconds):
                logger.warning(
                    "Dialpad event_timestamp missing or outside tolerance window "
                    "(tolerance=%ss); rejecting as a possible replay",
                    config.webhook_timestamp_tolerance_seconds,
                )
                raise HTTPException(status_code=401, detail="Stale or missing timestamp")
            return payload

        # No verification applies: plain JSON only. A JWT cannot be trusted
        # unverified, so refuse rather than decode it blindly.
        if _looks_like_jwt(text):
            logger.error("Dialpad sent a signed (JWT) event but DIALPAD_WEBHOOK_SECRET is not set")
            raise HTTPException(status_code=400, detail="Signed event but no secret configured")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.error("Failed to parse Dialpad JSON body")
            raise HTTPException(status_code=400, detail="Invalid JSON") from None

    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "vcon-dialpad-adapter"}

    @app.post("/webhook/call-event")
    async def call_event_webhook(request: Request):
        body = await request.body()
        # Verify, build and post do blocking I/O; keep them off the event loop.
        return await run_in_threadpool(process_event, body)

    def event_adds_something(record: dict[str, Any], event: dict[str, Any]) -> bool:
        """Whether an event for an already posted call could change its vCon."""
        if record.get("update_pending"):
            return True  # an earlier update failed; any event retries it
        state = event.get("state")
        if state in ("recording", VOICEMAIL_STATE):
            keys = {recording_key(d) for d in builder.select_recordings(event)}
            known = {recording_key(d) for d in record.get("recordings") or []}
            if not keys or keys - known:
                return True
            return bool(
                state == VOICEMAIL_STATE
                and event.get("transcription_text")
                and not record.get("voicemail_text")
            )
        if state == TRANSCRIPTION_STATE:
            return config.include_transcript and not record.get("transcript")
        if state in RECAP_STATES:
            return config.include_recap and RECAP_STATES[state] not in (record.get("recap") or {})
        return False

    def build_adds_something(record: dict[str, Any], result: DialpadBuild) -> bool:
        """Whether a rebuild carries anything the posted revision lacked."""
        known = {recording_key(d) for d in record.get("recordings") or []}
        return bool(
            {recording_key(d) for d in result.recordings} - known
            or (result.transcript and not record.get("transcript"))
            or set(result.recap) - set(record.get("recap") or {})
            or (result.voicemail_text and not record.get("voicemail_text"))
        )

    def process_event(body: bytes) -> dict[str, Any]:
        payload = decode_payload(body)
        if not isinstance(payload, dict):
            logger.error("Dialpad event payload is not a JSON object")
            raise HTTPException(status_code=400, detail="Invalid payload")

        state = payload.get("state")
        call_id = payload.get("call_id")
        if call_id is None or call_id == "":
            logger.warning(f"Dialpad {state} event has no call_id, ignoring")
            return {"status": "ignored"}
        if state not in BUILDABLE_STATES:
            logger.debug(f"Ignoring Dialpad event in state: {state}")
            return {"status": "ignored"}

        key = str(call_id)
        with call_locks.hold(key):
            return process_call_event(key, state, payload)

    def process_call_event(key: str, state: str, payload: dict[str, Any]) -> dict[str, Any]:
        record = load_record(key)
        status = (record or {}).get("status")
        posted = status == POSTED
        if status == ABANDONED or (
            posted and not (config.update_on_later_events and event_adds_something(record, payload))
        ):
            logger.info(f"Dialpad call {key}: {state} event adds nothing, skipping")
            return {"status": "already_processed"}

        record = record or {}
        attempts = int(record.get("attempts") or 0) + 1
        last_attempt = attempts >= MAX_ATTEMPTS
        revision = int(record.get("revision") or 0)

        def failed(error: str, result: DialpadBuild | None = None) -> dict[str, Any]:
            # A posted call keeps its posted content; the failed update is
            # retried by the next event for the call.
            updated = {**record, "attempts": attempts, "last_error": error, "state": state}
            if result is not None and not updated.get("vcon_uuid"):
                # Keep the uuid: the post may have landed despite the error.
                updated["vcon_uuid"] = result.vcon.uuid
                updated["created_at"] = result.vcon.created_at
            if posted:
                updated["update_pending"] = True
            if not last_attempt:
                updated["status"] = status or PENDING
                save_record(key, updated)
                raise HTTPException(status_code=503, detail=f"{error}; retry later")
            updated["status"] = ABANDONED
            save_record(key, updated)
            logger.error(
                f"Dialpad call {key}: giving up after {attempts} attempts ({error}); "
                f"vCon {updated.get('vcon_uuid') or '(none)'} will not be updated again"
            )
            return {"status": ABANDONED, **({"uuid": updated["vcon_uuid"]} if posted else {})}

        try:
            result = builder.build_call(
                payload,
                vcon_uuid=record.get("vcon_uuid"),
                created_at=record.get("created_at"),
                previous=record if posted else None,
                allow_partial=last_attempt,
                revision=revision + 1,
            )
        except DialpadBuildError as e:
            logger.error(f"Failed to build vCon for Dialpad call {key}: {e}")
            return failed("build_failed")
        if result is None:
            logger.info(f"Dialpad call {key}: nothing to build")
            return {"status": "ignored"}

        if posted and not build_adds_something(record, result):
            if result.partial:
                # Last attempt, and what this event was for still could not
                # be fetched: give up rather than leave the failure pending.
                return failed("fetch_failed")
            # E.g. call_transcription arrived but the transcript is not
            # served yet. Nothing to post and nothing failed, so the record
            # (including any update_pending flag) stays as it was.
            logger.info(f"Dialpad call {key}: {state} event added nothing new")
            return {"status": "already_processed"}

        vcon = result.vcon
        if not record.get("vcon_uuid"):
            # Persist the uuid before the first post: a crash after the post
            # lands but before the record below is written must not let the
            # next event mint a second uuid for the call.
            record = {
                **record,
                "vcon_uuid": vcon.uuid,
                "created_at": vcon.created_at,
                "status": status or PENDING,
                "state": state,
            }
            save_record(key, record)
        if not poster.post(vcon):
            logger.error(f"Failed to post vCon {vcon.uuid} for Dialpad call {key}")
            return failed("post_failed", result)

        new_record = {
            **result.snapshot(),
            "vcon_uuid": vcon.uuid,
            "created_at": vcon.created_at,
            "status": ABANDONED if result.partial else POSTED,
            "attempts": 0,
            "revision": revision + 1,
            "update_pending": False,
            "state": state,
        }
        save_record(key, new_record)
        if result.partial:
            logger.error(
                f"Dialpad call {key}: giving up after {attempts} attempts; posted vCon "
                f"{vcon.uuid} without the recordings that could not be retrieved"
            )
        return {"status": "ok" if revision == 0 else "updated", "uuid": vcon.uuid}

    @app.get("/status/{call_id}")
    async def get_call_status(call_id: str):
        """Get processing status for a call."""
        entry = load_record(call_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Call not found")
        return {
            "call_id": call_id,
            "vcon_uuid": entry.get("vcon_uuid") or None,
            "status": entry.get("status"),
            "revision": int(entry.get("revision") or 0),
        }

    return app
