"""Configuration management for Asterisk adapter."""

import os
from typing import Optional
from core.base_config import BaseConfig


class AsteriskConfig(BaseConfig):
    """Asterisk-specific configuration.

    Extends BaseConfig with Asterisk-specific settings for
    ARI (Asterisk REST Interface) connection and recording paths.
    """

    def __init__(self, env_file: Optional[str] = None):
        """Load configuration from environment.

        Args:
            env_file: Optional path to .env file
        """
        super().__init__(env_file)

        # State file specific to Asterisk
        self.state_file = os.getenv(
            "STATE_FILE",
            ".asterisk_adapter_state.json"
        )

        # Asterisk ARI connection settings
        self.asterisk_host = os.getenv("ASTERISK_HOST", "localhost")
        self.asterisk_ari_port = int(os.getenv("ASTERISK_ARI_PORT", "8088"))
        self.asterisk_ari_username = os.getenv("ASTERISK_ARI_USERNAME", "asterisk")
        self.asterisk_ari_password = os.getenv("ASTERISK_ARI_PASSWORD")

        # ARI base URL
        ari_scheme = os.getenv("ASTERISK_ARI_SCHEME", "http")
        self.asterisk_ari_url = os.getenv(
            "ASTERISK_ARI_URL",
            f"{ari_scheme}://{self.asterisk_host}:{self.asterisk_ari_port}"
        )

        # Recording storage location (for local file access)
        self.recordings_path = os.getenv(
            "ASTERISK_RECORDINGS_PATH",
            "/var/spool/asterisk/recording"
        )

        # Webhook authentication (optional)
        self.webhook_secret = os.getenv("ASTERISK_WEBHOOK_SECRET")

        # Whether to validate webhook signatures
        self.validate_webhook = os.getenv(
            "VALIDATE_ASTERISK_WEBHOOK",
            "false"
        ).lower() in ("true", "1", "yes")

    def get_ari_auth(self) -> tuple:
        """Get ARI authentication tuple.

        Returns:
            Tuple of (username, password) for ARI requests
        """
        return (self.asterisk_ari_username, self.asterisk_ari_password)
