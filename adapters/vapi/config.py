"""Configuration management for the VAPI adapter."""

import logging
import os

from core.base_config import BaseConfig

logger = logging.getLogger(__name__)


class VapiConfig(BaseConfig):
    """VAPI-specific configuration.

    VAPI (https://vapi.ai) posts an `end-of-call-report` server message when a
    voice-AI call finishes. Authentication is a shared secret VAPI echoes back
    in the `x-vapi-secret` header on every server-URL request it makes
    (https://docs.vapi.ai/server-url/server-authentication, "legacy" header
    form of its Bearer-token auth). VAPI also offers HMAC-signature
    authentication with a configurable header name, but the shared-secret
    header is the simpler, documented default and is what this adapter
    verifies with a constant-time comparison.
    """

    def __init__(self, env_file: str | None = None):
        super().__init__(env_file)

        self.state_file = os.getenv("STATE_FILE", ".vapi_adapter_state.json")

        # Party display names, since VAPI's end-of-call-report identifies
        # parties by role ("user"/"bot") rather than name.
        self.customer_party_name = os.getenv("VAPI_CUSTOMER_NAME", "Customer")
        self.agent_party_name = os.getenv("VAPI_AGENT_NAME", "AI Agent")

        # Shared secret VAPI sends back in the `x-vapi-secret` header.
        self.webhook_secret = os.getenv("VAPI_WEBHOOK_SECRET")

        self.validate_webhook = os.getenv("VALIDATE_VAPI_WEBHOOK", "true").lower() in (
            "true",
            "1",
            "yes",
        )

        if self.validate_webhook and not self.webhook_secret:
            if self.allow_unsigned_webhooks:
                logger.warning(
                    "VALIDATE_VAPI_WEBHOOK is enabled but VAPI_WEBHOOK_SECRET is not set; "
                    "accepting unsigned webhooks because ALLOW_UNSIGNED_WEBHOOKS=true"
                )
            else:
                raise ValueError(
                    "VAPI_WEBHOOK_SECRET is required when VALIDATE_VAPI_WEBHOOK is true. "
                    "Set it to the secret configured on the VAPI server URL, or set "
                    "ALLOW_UNSIGNED_WEBHOOKS=true to accept unsigned webhooks (not "
                    "recommended)."
                )
