"""Configuration management for the Dialpad adapter.

Dialpad call event subscriptions
(https://developers.dialpad.com/docs/call-events) POST a call event (a JWT
signed with the subscription secret, or plain JSON when no secret is set) to
this adapter. The event only references the recording, so every vCon build
needs the Dialpad API to fetch the audio; `DIALPAD_API_KEY` is therefore
required.
"""

import logging
import os

from core.base_config import BaseConfig
from core.webhook_security import parse_tolerance_seconds

from .client import DialpadClient

logger = logging.getLogger(__name__)

VALID_TRIGGER_STATES = ("recording", "call_transcription")


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("true", "1", "yes")


class DialpadConfig(BaseConfig):
    """Dialpad-specific configuration."""

    def __init__(self, env_file: str | None = None):
        super().__init__(env_file)

        self.state_file = os.getenv("STATE_FILE", ".dialpad_adapter_state.json")

        self.api_key = os.getenv("DIALPAD_API_KEY")
        if not self.api_key:
            raise ValueError(
                "DIALPAD_API_KEY environment variable is required: the call event "
                "carries no audio, so every vCon build downloads it from the Dialpad API"
            )
        # `https://sandbox.dialpad.com` for the sandbox.
        self.api_base = os.getenv("DIALPAD_API_BASE", "https://dialpad.com").rstrip("/")

        # Secret configured on the Dialpad event subscription.
        self.webhook_secret = os.getenv("DIALPAD_WEBHOOK_SECRET")
        self.validate_webhook = _env_bool("VALIDATE_DIALPAD_SIGNATURE", "true")

        # Reject events whose timestamp is too old (or too far in the future),
        # to bound replay exposure. Dialpad sends `recording` events after the
        # recording finishes processing and does not document its retry
        # timing, so the provider default is a generous hour rather than the
        # shared 300s, which could drop legitimate delayed deliveries.
        #
        # Resolution order:
        #   1. DIALPAD_WEBHOOK_TOLERANCE_SECONDS, if set;
        #   2. else WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS, if explicitly set;
        #   3. else 3600.
        dialpad_tolerance_raw = os.getenv("DIALPAD_WEBHOOK_TOLERANCE_SECONDS")
        if dialpad_tolerance_raw is not None:
            self.webhook_timestamp_tolerance_seconds = parse_tolerance_seconds(
                dialpad_tolerance_raw, source="DIALPAD_WEBHOOK_TOLERANCE_SECONDS"
            )
        elif not self.webhook_timestamp_tolerance_explicitly_set:
            self.webhook_timestamp_tolerance_seconds = 3600

        # Which event state builds the vCon. `recording` fires when the audio
        # is ready; `call_transcription` fires later, once Dialpad AI has the
        # transcript, so the vCon is complete in one pass.
        self.trigger_state = os.getenv("DIALPAD_TRIGGER_STATE", "recording").strip().lower()
        if self.trigger_state not in VALID_TRIGGER_STATES:
            raise ValueError(
                f"DIALPAD_TRIGGER_STATE must be one of {list(VALID_TRIGGER_STATES)}, "
                f"got: {self.trigger_state}"
            )

        self.include_transcript = _env_bool("DIALPAD_INCLUDE_TRANSCRIPT", "true")
        self.include_recap = _env_bool("DIALPAD_INCLUDE_RECAP", "true")
        self.include_admin_recordings = _env_bool("DIALPAD_INCLUDE_ADMIN_RECORDINGS", "false")

        if self.validate_webhook and not self.webhook_secret:
            if self.allow_unsigned_webhooks:
                logger.warning(
                    "VALIDATE_DIALPAD_SIGNATURE is enabled but DIALPAD_WEBHOOK_SECRET "
                    "is not set; accepting unsigned webhooks because "
                    "ALLOW_UNSIGNED_WEBHOOKS=true"
                )
            else:
                raise ValueError(
                    "DIALPAD_WEBHOOK_SECRET is required when VALIDATE_DIALPAD_SIGNATURE "
                    "is true. Set it to the secret on the Dialpad event subscription, or "
                    "set ALLOW_UNSIGNED_WEBHOOKS=true to accept unsigned webhooks (not "
                    "recommended)."
                )

    def build_client(self) -> DialpadClient:
        """Construct the Dialpad API client for this configuration."""
        return DialpadClient(api_key=self.api_key, api_base=self.api_base)
