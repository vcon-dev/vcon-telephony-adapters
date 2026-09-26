"""Configuration management for the ElevenLabs adapter.

ElevenLabs Conversational AI supports post-call webhooks
(https://elevenlabs.io/docs/conversational-ai/guides/webhooks), authenticated
with an HMAC signature in the `elevenlabs-signature` header. This adapter
receives that webhook directly rather than polling the conversations API, the
same shape as every other webhook-based adapter in this monorepo.

Note: the earlier `rescue/cursor-multi-mode-wip` draft of this adapter polled
`GET /v1/convai/conversations` on an interval instead. ElevenLabs' own
webhook is the more direct integration (no poll lag, richer payload, and it
is the platform's documented mechanism for this), so this version uses it;
`ELEVENLABS_API_KEY` is kept as an optional setting in case a future version
needs to fetch full conversation detail the webhook payload doesn't carry.
"""

import logging
import os

from core.base_config import BaseConfig

logger = logging.getLogger(__name__)


class ElevenLabsConfig(BaseConfig):
    """ElevenLabs-specific configuration."""

    def __init__(self, env_file: str | None = None):
        super().__init__(env_file)

        self.state_file = os.getenv("STATE_FILE", ".elevenlabs_adapter_state.json")

        # Optional: only needed if a deployment wants to fetch additional
        # conversation detail from the API beyond what the webhook carries.
        self.api_key = os.getenv("ELEVENLABS_API_KEY")
        self.api_base = os.getenv("ELEVENLABS_API_BASE", "https://api.elevenlabs.io/v1")

        # HMAC secret configured in the ElevenLabs dashboard for this webhook.
        self.webhook_secret = os.getenv("ELEVENLABS_WEBHOOK_SECRET")

        self.validate_webhook = os.getenv("VALIDATE_ELEVENLABS_WEBHOOK", "true").lower() in (
            "true",
            "1",
            "yes",
        )

        # Reject webhooks whose timestamp is older than this, to bound replay
        # exposure. ElevenLabs' own SDK helpers validate the timestamp too;
        # since this adapter verifies the signature by hand (see webhook.py),
        # it re-implements that check.
        self.webhook_tolerance_seconds = int(
            os.getenv("ELEVENLABS_WEBHOOK_TOLERANCE_SECONDS", "1800")
        )

        if self.validate_webhook and not self.webhook_secret:
            if self.allow_unsigned_webhooks:
                logger.warning(
                    "VALIDATE_ELEVENLABS_WEBHOOK is enabled but ELEVENLABS_WEBHOOK_SECRET "
                    "is not set; accepting unsigned webhooks because "
                    "ALLOW_UNSIGNED_WEBHOOKS=true"
                )
            else:
                raise ValueError(
                    "ELEVENLABS_WEBHOOK_SECRET is required when VALIDATE_ELEVENLABS_WEBHOOK "
                    "is true. Set it to the signing secret shown when the post-call webhook "
                    "is configured in the ElevenLabs dashboard, or set "
                    "ALLOW_UNSIGNED_WEBHOOKS=true to accept unsigned webhooks (not "
                    "recommended)."
                )
