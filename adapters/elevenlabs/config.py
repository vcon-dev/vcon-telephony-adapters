"""ElevenLabs adapter configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class ElevenLabsConfig:
    api_key: str
    api_base: str = "https://api.elevenlabs.io/v1"
    webhook_url: str = ""
    webhook_auth_header_name: str = "x-conserver-api-token"
    webhook_auth_header_value: str | None = None
    poll_interval_seconds: int = 300
    log_level: str = "INFO"
    state_file: str = ".elevenlabs_adapter_state.json"

    @classmethod
    def from_env(cls) -> "ElevenLabsConfig":
        return cls(
            api_key=os.environ["ELEVENLABS_API_KEY"],
            api_base=os.environ.get("ELEVENLABS_API_BASE", "https://api.elevenlabs.io/v1"),
            webhook_url=os.environ.get("WEBHOOK_URL", ""),
            webhook_auth_header_name=os.environ.get(
                "WEBHOOK_AUTH_HEADER_NAME", "x-conserver-api-token"
            ),
            webhook_auth_header_value=os.environ.get("WEBHOOK_AUTH_HEADER_VALUE"),
            poll_interval_seconds=int(os.environ.get("POLL_INTERVAL", "300")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            state_file=os.environ.get("STATE_FILE", ".elevenlabs_adapter_state.json"),
        )
