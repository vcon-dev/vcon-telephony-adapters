"""VAPI webhook receiver — receives end-of-call reports, builds + ships vCons."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException

from core.poster import HttpPoster

from .builder import VapiVconBuilder
from .config import VapiConfig

logger = logging.getLogger(__name__)


def create_app(config: VapiConfig, builder: VapiVconBuilder | None = None) -> FastAPI:
    """Create a FastAPI app exposing the VAPI webhook endpoint."""
    app = FastAPI(title="vCon VAPI Adapter")

    headers = {"Content-Type": "application/json"}
    if config.webhook_auth_header_value:
        headers[config.webhook_auth_header_name] = config.webhook_auth_header_value
    poster = HttpPoster(url=config.webhook_url, headers=headers) if config.webhook_url else None

    builder = builder or VapiVconBuilder(
        customer_party_name=config.customer_party_name,
        agent_party_name=config.agent_party_name,
    )

    @app.post("/vapi")
    async def vapi(data: dict) -> dict:
        if "message" not in data:
            raise HTTPException(status_code=422, detail="Missing 'message' key in payload")

        vcon = builder.build(data["message"])
        if vcon is None:
            # Not an end-of-call message (e.g. status update) — ack and ignore.
            return {"status": "ignored"}

        if poster is not None:
            if poster.post(vcon):
                return {"status": "ok", "uuid": vcon.uuid}
            return {"status": "post_failed", "uuid": vcon.uuid}

        # No webhook configured — just return the constructed vCon for debugging.
        return {"status": "ok", "uuid": vcon.uuid, "vcon": vcon.vcon_dict}

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    return app
