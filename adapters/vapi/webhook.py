"""FastAPI webhook receiver for VAPI server messages."""

from __future__ import annotations

import hmac
import logging

from fastapi import FastAPI, Header, HTTPException, Request

from core.poster import HttpPoster
from core.tracker import StateTracker

from .builder import VapiVconBuilder
from .config import VapiConfig

logger = logging.getLogger(__name__)


def create_app(config: VapiConfig) -> FastAPI:
    """Create and configure the FastAPI application for the VAPI adapter."""
    app = FastAPI(
        title="vCon VAPI Adapter",
        description="Receives VAPI end-of-call-report webhooks and creates vCons",
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

    builder = VapiVconBuilder(
        customer_party_name=config.customer_party_name,
        agent_party_name=config.agent_party_name,
        download_recordings=config.download_recordings,
        publisher=publisher,
        lawful_basis=lawful_basis,
    )
    poster = HttpPoster(config.conserver_url, config.get_headers(), config.ingress_lists)
    tracker = StateTracker(config.state_file)

    def validate_secret(secret_header: str | None) -> bool:
        """Constant-time comparison of the `x-vapi-secret` header.

        Fails closed: if validation is on and no secret is configured, the
        adapter refuses to start (see VapiConfig), so this is only ever
        reached with a secret configured, or with ALLOW_UNSIGNED_WEBHOOKS set.
        """
        if not config.validate_webhook:
            return True
        if not config.webhook_secret:
            # Only reachable with ALLOW_UNSIGNED_WEBHOOKS=true.
            return True
        if not secret_header:
            return False
        return hmac.compare_digest(
            secret_header.encode("utf-8"), config.webhook_secret.encode("utf-8")
        )

    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "vcon-vapi-adapter"}

    @app.post("/vapi")
    async def vapi_webhook(
        request: Request,
        x_vapi_secret: str | None = Header(default=None),
    ):
        if not validate_secret(x_vapi_secret):
            logger.warning("Invalid VAPI webhook secret")
            raise HTTPException(status_code=401, detail="Invalid secret")

        try:
            data = await request.json()
        except Exception:
            logger.error("Failed to parse JSON body")
            raise HTTPException(status_code=400, detail="Invalid JSON") from None

        if "message" not in data:
            raise HTTPException(status_code=422, detail="Missing 'message' key in payload")

        message = data["message"]
        call = message.get("call")
        call_id = call.get("id") if isinstance(call, dict) else None

        if message.get("type") != "end-of-call-report":
            return {"status": "ignored"}

        if call_id and tracker.is_processed(call_id):
            logger.info(f"VAPI call {call_id} already processed, skipping")
            return {"status": "already_processed"}

        vcon = builder.build(message)
        if vcon is None:
            return {"status": "ignored"}

        success = poster.post(vcon)
        if call_id:
            tracker.mark_processed(
                call_id,
                vcon.uuid,
                status="success" if success else "post_failed",
            )

        if success:
            return {"status": "ok", "uuid": vcon.uuid}
        return {"status": "post_failed", "uuid": vcon.uuid}

    return app
