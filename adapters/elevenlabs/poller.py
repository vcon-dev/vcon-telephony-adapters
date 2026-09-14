"""ElevenLabs poller — fetches recent conversations from the ElevenLabs API.

Minimal pull-mode integration: each iteration queries the conversations endpoint
for any new completed conversations, parses them into the `Conversation` pydantic
model, builds a vCon, and ships it via `core.poster.HttpPoster`.

Real-world deployments will want to maintain a since-cursor on the API response
(not implemented here — the user's existing vcon-eleven-labs-adapter handles
this and is a richer source). Treat this poller as a thin reference port; the
builder is the part that matters.
"""

from __future__ import annotations

import logging
import signal
import time

import requests

from core.poster import HttpPoster

from .builder import ElevenLabsVconBuilder
from .config import ElevenLabsConfig
from .models import Conversation

logger = logging.getLogger(__name__)


class ElevenLabsPoller:
    def __init__(
        self,
        config: ElevenLabsConfig,
        builder: ElevenLabsVconBuilder | None = None,
        poster: HttpPoster | None = None,
    ):
        self.config = config
        self.builder = builder or ElevenLabsVconBuilder()
        headers = {"Content-Type": "application/json"}
        if config.webhook_auth_header_value:
            headers[config.webhook_auth_header_name] = config.webhook_auth_header_value
        self.poster = poster or HttpPoster(url=config.webhook_url, headers=headers)
        self._running = True

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)
        while self._running:
            try:
                self.process_once()
            except Exception as e:
                logger.exception(f"elevenlabs poll error: {e}")
            self._sleep(self.config.poll_interval_seconds)

    def process_once(self) -> int:
        """Single poll iteration. Returns count of vCons shipped."""
        conversations = self._fetch_recent_conversations()
        shipped = 0
        for raw in conversations:
            conversation = Conversation.model_validate(raw)
            vcon = self.builder.build(conversation)
            if self.poster.post(vcon):
                shipped += 1
        return shipped

    def _fetch_recent_conversations(self) -> list[dict]:
        url = f"{self.config.api_base.rstrip('/')}/convai/conversations"
        r = requests.get(
            url,
            headers={"xi-api-key": self.config.api_key},
            params={"limit": 50},
            timeout=30,
        )
        r.raise_for_status()
        # ElevenLabs returns {"conversations": [...]}; defensive .get
        return r.json().get("conversations", [])

    def _stop(self, *_args) -> None:
        self._running = False

    def _sleep(self, seconds: int) -> None:
        for _ in range(seconds):
            if not self._running:
                return
            time.sleep(1)
