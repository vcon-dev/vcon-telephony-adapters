"""FastAPI app for the SignalWire adapter.

SignalWire is polled, not pushed to (see `config.py` and `poller.py`), so
there is no webhook endpoint here. This module exists so `python main.py
signalwire` and the Docker image behave like every other adapter: a
`/health` endpoint the container HEALTHCHECK can probe, with the poller
itself running on a background thread started at app startup.
"""

from __future__ import annotations

import logging
import threading

from fastapi import FastAPI

from .config import SignalWireConfig
from .poller import SignalWirePoller

logger = logging.getLogger(__name__)


def create_app(config: SignalWireConfig) -> FastAPI:
    app = FastAPI(
        title="vCon SignalWire Adapter",
        description="Polls SignalWire for new recordings and creates vCons",
        version="0.1.0",
    )

    poller = SignalWirePoller(config)
    app.state.poller = poller

    @app.on_event("startup")
    def _start_poller() -> None:
        thread = threading.Thread(target=poller.run, name="signalwire-poller", daemon=True)
        thread.start()
        logger.info(f"SignalWire poller started (interval={config.poll_interval_seconds}s)")

    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "vcon-signalwire-adapter"}

    return app
