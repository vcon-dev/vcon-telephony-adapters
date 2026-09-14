"""SignalWire adapter configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class SignalWireConfig:
    """Configuration for the SignalWire poller.

    Read from environment variables. Required:
      SIGNALWIRE_PROJECT_ID, SIGNALWIRE_AUTH_TOKEN, SIGNALWIRE_SPACE_URL, WEBHOOK_URL
    """

    project_id: str
    auth_token: str
    space_url: str
    webhook_url: str
    webhook_auth_header_name: str = "x-conserver-api-token"
    webhook_auth_header_value: str | None = None
    poll_interval_seconds: int = 300
    debug_mode: bool = False
    debug_dir: str = "vcon_debug"
    s3_enabled: bool = False
    s3_bucket: str | None = None
    s3_key_prefix: str = "recordings/"
    s3_presign_expiry: int = 604800
    processed_calls_file: str = "processed_calls.json"
    retention_days: int = 30
    state_file: str = ".signalwire_adapter_state.json"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "SignalWireConfig":
        return cls(
            project_id=os.environ["SIGNALWIRE_PROJECT_ID"],
            auth_token=os.environ["SIGNALWIRE_AUTH_TOKEN"],
            space_url=os.environ["SIGNALWIRE_SPACE_URL"],
            webhook_url=os.environ.get("WEBHOOK_URL", ""),
            webhook_auth_header_name=os.environ.get(
                "WEBHOOK_AUTH_HEADER_NAME", "x-conserver-api-token"
            ),
            webhook_auth_header_value=os.environ.get("WEBHOOK_AUTH_HEADER_VALUE"),
            poll_interval_seconds=int(os.environ.get("POLL_INTERVAL", "300")),
            debug_mode=os.environ.get("DEBUG_MODE", "false").lower() == "true",
            debug_dir=os.environ.get("DEBUG_DIR", "vcon_debug"),
            s3_enabled=os.environ.get("S3_ENABLED", "false").lower() == "true",
            s3_bucket=os.environ.get("S3_BUCKET"),
            s3_key_prefix=os.environ.get("S3_KEY_PREFIX", "recordings/"),
            s3_presign_expiry=int(os.environ.get("S3_PRESIGN_EXPIRY", "604800")),
            processed_calls_file=os.environ.get(
                "PROCESSED_CALLS_FILE", "processed_calls.json"
            ),
            retention_days=int(os.environ.get("RETENTION_DAYS", "30")),
            state_file=os.environ.get("STATE_FILE", ".signalwire_adapter_state.json"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
