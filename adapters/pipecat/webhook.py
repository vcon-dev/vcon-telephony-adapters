"""Minimal FastAPI app for the Pipecat adapter.

Pipecat is not a webhook sender: there is no inbound event to receive (see
`config.py`). This module exists so `python main.py pipecat` and the Docker
image behave like every other adapter — health check included, no
credentials required — while the real integration point is importing
`VconConversationObserver` (see `observer.py`) into a Pipecat pipeline
running elsewhere.
"""

from __future__ import annotations

from fastapi import FastAPI

from .config import PipecatConfig


def create_app(config: PipecatConfig) -> FastAPI:
    app = FastAPI(
        title="vCon Pipecat Adapter",
        description=(
            "Pipecat has no inbound webhook. Import "
            "adapters.pipecat.VconConversationObserver into your Pipecat "
            "pipeline to emit vCons; this process only exposes a health check."
        ),
        version="0.1.0",
    )

    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "vcon-pipecat-adapter"}

    return app
