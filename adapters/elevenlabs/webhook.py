"""FastAPI webhook receiver for ElevenLabs post-call events.

Signature scheme: UNVERIFIED exact byte format. ElevenLabs' docs
(https://elevenlabs.io/docs/conversational-ai/guides/webhooks) confirm the
header name (`elevenlabs-signature`) and that it is an HMAC signature with a
timestamp, verified in their SDKs via
`elevenlabs.webhooks.construct_event(raw_body, sig_header, secret)`, but the
public docs available to this change do not spell out the signed-string
format, separator, or hash encoding. This implementation follows the
widely-used Stripe-style convention their header shape (`t=<ts>,v0=<hex>`)
strongly resembles: HMAC-SHA256 over `f"{timestamp}.{body}"`, hex-encoded,
constant-time compared. Verify this against a real signed request (or the
`elevenlabs` Python SDK's `webhooks.construct_event`) before relying on it in
production; if it's wrong, this fails closed (rejects), it does not silently
accept.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

from fastapi import FastAPI, Header, HTTPException, Request

from core.poster import HttpPoster
from core.tracker import StateTracker

from .builder import ElevenLabsVconBuilder
from .config import ElevenLabsConfig

logger = logging.getLogger(__name__)


def _parse_signature_header(header_value: str) -> tuple[str | None, str | None]:
    """Parse a `t=<timestamp>,v0=<hex_hmac>` style header."""
    timestamp = None
    signature = None
    for part in header_value.split(","):
        part = part.strip()
        if part.startswith("t="):
            timestamp = part[2:]
        elif part.startswith("v0="):
            signature = part[3:]
    return timestamp, signature


def create_app(config: ElevenLabsConfig) -> FastAPI:
    """Create and configure the FastAPI application for the ElevenLabs adapter."""
    app = FastAPI(
        title="vCon ElevenLabs Adapter",
        description="Receives ElevenLabs post-call webhooks and creates vCons",
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

    builder = ElevenLabsVconBuilder(publisher=publisher, lawful_basis=lawful_basis)
    poster = HttpPoster(config.conserver_url, config.get_headers(), config.ingress_lists)
    tracker = StateTracker(config.state_file)

    def validate_signature(body: bytes, signature_header: str | None) -> bool:
        if not config.validate_webhook:
            return True
        if not config.webhook_secret:
            # Only reachable with ALLOW_UNSIGNED_WEBHOOKS=true.
            return True
        if not signature_header:
            return False

        timestamp, signature = _parse_signature_header(signature_header)
        if not timestamp or not signature:
            return False

        try:
            timestamp_int = int(timestamp)
        except ValueError:
            return False

        if abs(time.time() - timestamp_int) > config.webhook_tolerance_seconds:
            logger.warning("ElevenLabs webhook timestamp outside tolerance window")
            return False

        signed_payload = f"{timestamp}.".encode() + body
        expected = hmac.new(
            config.webhook_secret.encode("utf-8"), signed_payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "vcon-elevenlabs-adapter"}

    @app.post("/webhook/post-call")
    async def post_call_webhook(
        request: Request,
        elevenlabs_signature: str | None = Header(default=None),
    ):
        body = await request.body()

        if not validate_signature(body, elevenlabs_signature):
            logger.warning("Invalid ElevenLabs webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

        try:
            event = await request.json()
        except Exception:
            logger.error("Failed to parse JSON body")
            raise HTTPException(status_code=400, detail="Invalid JSON") from None

        event_type = event.get("type")
        data = event.get("data") or {}
        conversation_id = data.get("conversation_id")

        dedupe_key = f"{conversation_id}:{event_type}" if conversation_id else None
        if dedupe_key and tracker.is_processed(dedupe_key):
            logger.info(f"ElevenLabs event {dedupe_key} already processed, skipping")
            return {"status": "already_processed"}

        vcon = builder.build(event)
        if vcon is None:
            return {"status": "ignored"}

        success = poster.post(vcon)
        if dedupe_key:
            tracker.mark_processed(
                dedupe_key,
                vcon.uuid,
                status="success" if success else "post_failed",
            )

        if success:
            return {"status": "ok", "uuid": vcon.uuid}
        return {"status": "post_failed", "uuid": vcon.uuid}

    return app
