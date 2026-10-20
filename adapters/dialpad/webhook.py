"""FastAPI webhook receiver for Dialpad call events.

Dialpad call event subscriptions
(https://developers.dialpad.com/docs/call-events) POST the event as a compact
JWT signed HS256 with the subscription secret, or as plain JSON when the
subscription has no secret. Events arrive out of order and each call state is
a separate event, so the receiver builds on one configured state
(`DIALPAD_TRIGGER_STATE`) and on `voicemail_uploaded`, and ignores the rest.

Whether the JWT carries `iat`/`exp` is undocumented, so neither is required.
Replay protection uses the event's own `event_timestamp` (unix milliseconds),
checked only after the signature has authenticated the payload.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import jwt
from fastapi import FastAPI, HTTPException, Request

from core.poster import HttpPoster
from core.tracker import StateTracker
from core.webhook_security import is_timestamp_fresh

from .builder import DialpadBuildError, DialpadVconBuilder
from .config import DialpadConfig

logger = logging.getLogger(__name__)

VOICEMAIL_STATE = "voicemail_uploaded"
TRANSCRIPTION_STATE = "call_transcription"

# Retry policy. A failed build or post is recorded but does not block the
# call: the next delivery of the same key tries again. The first
# MAX_ATTEMPTS - 1 failures answer 503 so Dialpad can redeliver (its retry
# policy is undocumented); after that the adapter answers 200 so a broken call
# cannot hold Dialpad in a retry loop, and the last attempt keeps whatever
# recordings did arrive. Redeliveries are also bounded by the replay window,
# since they carry the original event_timestamp. With DIALPAD_TRIGGER_STATE=
# recording, a later call_transcription event is a second chance for a call
# whose recording build failed. Twilio and ElevenLabs instead ack 200 and
# record the failure as final; Dialpad events only reference the audio, so a
# transient API or download failure there would otherwise lose the call.
MAX_ATTEMPTS = 5
RETRYABLE_STATUSES = ("build_failed", "post_failed")

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

    def decode_payload(body: bytes) -> Any:
        """Authenticate and decode the request body into the event payload.

        Raises HTTPException: 401 for a bad signature or a stale event, 400
        for a body that cannot be decoded.
        """
        try:
            text = body.decode("utf-8").strip()
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Invalid body") from None

        if config.validate_webhook and config.webhook_secret:
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

        # Validation off, or no secret (only reachable with
        # ALLOW_UNSIGNED_WEBHOOKS=true): plain JSON only. A JWT cannot be
        # trusted unverified, so refuse rather than decode it blindly.
        if _looks_like_jwt(text):
            logger.error(
                "Received a signed (JWT) Dialpad event but no secret is "
                "configured to verify it; set DIALPAD_WEBHOOK_SECRET"
            )
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
        payload = decode_payload(await request.body())
        if not isinstance(payload, dict):
            logger.error("Dialpad event payload is not a JSON object")
            raise HTTPException(status_code=400, detail="Invalid payload")

        state = payload.get("state")
        call_id = payload.get("call_id")
        if call_id is None or call_id == "":
            logger.warning(f"Dialpad {state} event has no call_id, ignoring")
            return {"status": "ignored"}

        if state == config.trigger_state:
            dedupe_key = str(call_id)
        elif state == VOICEMAIL_STATE:
            dedupe_key = f"{call_id}:voicemail"
        elif (
            state == TRANSCRIPTION_STATE
            and config.trigger_state == "recording"
            and tracker.get_processing_status(str(call_id)) in RETRYABLE_STATUSES
        ):
            # The recording build for this call failed; try again from here.
            dedupe_key = str(call_id)
        else:
            logger.debug(f"Ignoring Dialpad event in state: {state}")
            return {"status": "ignored"}

        entry = tracker.get_metadata(dedupe_key)
        if entry is not None and entry.get("status") not in RETRYABLE_STATUSES:
            logger.info(f"Dialpad event {dedupe_key} already processed, skipping")
            return {"status": "already_processed"}
        attempts = int((entry or {}).get("attempts") or 0) + 1
        last_attempt = attempts >= MAX_ATTEMPTS

        def failed(status: str, vcon_uuid: str = "") -> dict[str, Any]:
            tracker.mark_processed(
                dedupe_key, vcon_uuid, status=status, state=state, attempts=attempts
            )
            if not last_attempt:
                raise HTTPException(status_code=503, detail=f"{status}; retry later")
            logger.error(f"Dialpad {dedupe_key}: giving up after {attempts} attempts")
            result = {"status": status}
            if vcon_uuid:
                result["uuid"] = vcon_uuid
            return result

        try:
            vcon = builder.build(payload, allow_partial=last_attempt)
        except DialpadBuildError as e:
            logger.error(f"Failed to build vCon for Dialpad call {call_id}: {e}")
            return failed("build_failed")
        if vcon is None:
            logger.info(f"Dialpad call {call_id}: nothing to build")
            return {"status": "ignored"}

        if not poster.post(vcon):
            logger.error(f"Failed to post vCon {vcon.uuid} for Dialpad call {call_id}")
            return failed("post_failed", vcon.uuid)
        tracker.mark_processed(
            dedupe_key, vcon.uuid, status="success", state=state, attempts=attempts
        )
        return {"status": "ok", "uuid": vcon.uuid}

    @app.get("/status/{call_id}")
    async def get_call_status(call_id: str):
        """Get processing status for a call (voicemails use `<call_id>:voicemail`)."""
        if not tracker.is_processed(call_id):
            raise HTTPException(status_code=404, detail="Call not found")
        return {
            "call_id": call_id,
            "vcon_uuid": tracker.get_vcon_uuid(call_id),
            "status": tracker.get_processing_status(call_id),
        }

    return app
