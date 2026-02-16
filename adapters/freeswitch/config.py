"""Configuration management for FreeSWITCH adapter."""

import os
from typing import Optional
from core.base_config import BaseConfig


class FreeSwitchConfig(BaseConfig):
    """FreeSWITCH-specific configuration.

    Extends BaseConfig with FreeSWITCH-specific settings for
    Event Socket Library (ESL) connection and recording paths.
    """

    def __init__(self, env_file: Optional[str] = None):
        """Load configuration from environment.

        Args:
            env_file: Optional path to .env file
        """
        super().__init__(env_file)

        # State file specific to FreeSWITCH
        self.state_file = os.getenv(
            "STATE_FILE",
            ".freeswitch_adapter_state.json"
        )

        # FreeSWITCH Event Socket connection (for optional ESL integration)
        self.freeswitch_host = os.getenv("FREESWITCH_HOST", "localhost")
        self.freeswitch_esl_port = int(os.getenv("FREESWITCH_ESL_PORT", "8021"))
        self.freeswitch_esl_password = os.getenv(
            "FREESWITCH_ESL_PASSWORD",
            "ClueCon"
        )

        # Recording storage location (for local file access)
        self.recordings_path = os.getenv(
            "FREESWITCH_RECORDINGS_PATH",
            "/var/lib/freeswitch/recordings"
        )

        # Recording URL base (if serving recordings via HTTP)
        self.recordings_url_base = os.getenv("FREESWITCH_RECORDINGS_URL_BASE")

        # Webhook authentication (optional shared secret)
        self.webhook_secret = os.getenv("FREESWITCH_WEBHOOK_SECRET")

        # Whether to validate webhook signatures
        self.validate_webhook = os.getenv(
            "VALIDATE_FREESWITCH_WEBHOOK",
            "false"
        ).lower() in ("true", "1", "yes")
