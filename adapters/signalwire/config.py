"""Configuration for the SignalWire adapter.

SignalWire's Compatibility API doesn't push a recording-complete webhook the
way Twilio does (there is a status-callback per call, but no equivalent for
"a new recording exists" across a poll window), so this adapter polls
`GET /Recordings.json` on an interval instead of receiving a webhook. There
is therefore no inbound request to authenticate; `SIGNALWIRE_AUTH_TOKEN` is
outbound Basic Auth to SignalWire's own API, not a webhook secret.
"""

import os

from core.base_config import BaseConfig


class SignalWireConfig(BaseConfig):
    """SignalWire-specific configuration."""

    def __init__(self, env_file: str | None = None):
        super().__init__(env_file)

        self.state_file = os.getenv("STATE_FILE", ".signalwire_adapter_state.json")

        self.project_id = os.getenv("SIGNALWIRE_PROJECT_ID")
        self.auth_token = os.getenv("SIGNALWIRE_AUTH_TOKEN")
        self.space_url = os.getenv("SIGNALWIRE_SPACE_URL")

        if not self.project_id or not self.auth_token or not self.space_url:
            raise ValueError(
                "SIGNALWIRE_PROJECT_ID, SIGNALWIRE_AUTH_TOKEN and SIGNALWIRE_SPACE_URL "
                "are all required"
            )

        self.poll_interval_seconds = int(os.getenv("SIGNALWIRE_POLL_INTERVAL_SECONDS", "300"))
        self.retention_days = int(os.getenv("SIGNALWIRE_RETENTION_DAYS", "30"))

    def api_base(self) -> str:
        return f"{self.space_url.rstrip('/')}/api/laml/2010-04-01/Accounts/{self.project_id}"

    def api_auth(self) -> tuple[str, str]:
        return (self.project_id, self.auth_token)
