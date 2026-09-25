"""Configuration management for FreeSWITCH adapter."""

import logging
import os

from core.base_config import BaseConfig

logger = logging.getLogger(__name__)


class FreeSwitchConfig(BaseConfig):
    """FreeSWITCH-specific configuration.

    Extends BaseConfig with FreeSWITCH-specific settings for
    Event Socket Library (ESL) connection and recording paths.
    """

    def __init__(self, env_file: str | None = None):
        """Load configuration from environment.

        Args:
            env_file: Optional path to .env file
        """
        super().__init__(env_file)

        # State file specific to FreeSWITCH
        self.state_file = os.getenv("STATE_FILE", ".freeswitch_adapter_state.json")

        # FreeSWITCH Event Socket connection (for optional ESL integration).
        # No stock default for the password: FreeSWITCH ships with "ClueCon"
        # out of the box, and defaulting to it here would mean any deployment
        # that enables the ESL path without setting its own password is
        # trivially reachable with the password every FreeSWITCH install
        # documentation uses. Required only if/when the ESL path is used.
        self.freeswitch_host = os.getenv("FREESWITCH_HOST", "localhost")
        self.freeswitch_esl_port = int(os.getenv("FREESWITCH_ESL_PORT", "8021"))
        self.freeswitch_esl_password = os.getenv("FREESWITCH_ESL_PASSWORD")

        # Recording storage location (for local file access)
        self.recordings_path = os.getenv(
            "FREESWITCH_RECORDINGS_PATH", "/var/lib/freeswitch/recordings"
        )

        # Recording URL base (if serving recordings via HTTP)
        self.recordings_url_base = os.getenv("FREESWITCH_RECORDINGS_URL_BASE")

        # Webhook authentication (required unless ALLOW_UNSIGNED_WEBHOOKS is set)
        self.webhook_secret = os.getenv("FREESWITCH_WEBHOOK_SECRET")

        # Whether to validate webhook signatures. On by default: an adapter
        # that accepts unauthenticated recording events by default is not a
        # safe default.
        self.validate_webhook = os.getenv("VALIDATE_FREESWITCH_WEBHOOK", "true").lower() in (
            "true",
            "1",
            "yes",
        )

        if self.validate_webhook and not self.webhook_secret:
            if self.allow_unsigned_webhooks:
                logger.warning(
                    "VALIDATE_FREESWITCH_WEBHOOK is enabled but FREESWITCH_WEBHOOK_SECRET "
                    "is not set; accepting unsigned webhooks because "
                    "ALLOW_UNSIGNED_WEBHOOKS=true"
                )
            else:
                raise ValueError(
                    "FREESWITCH_WEBHOOK_SECRET is required when VALIDATE_FREESWITCH_WEBHOOK "
                    "is true. Set the secret, or set ALLOW_UNSIGNED_WEBHOOKS=true to accept "
                    "unsigned webhooks (not recommended)."
                )
