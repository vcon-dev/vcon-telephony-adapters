"""Configuration management for Telnyx adapter."""

import os
from typing import Optional
from core.base_config import BaseConfig


class TelnyxConfig(BaseConfig):
    """Telnyx-specific configuration.

    Extends BaseConfig with Telnyx-specific settings for
    API authentication and webhook validation.
    """

    def __init__(self, env_file: Optional[str] = None):
        """Load configuration from environment.

        Args:
            env_file: Optional path to .env file
        """
        super().__init__(env_file)

        # State file specific to Telnyx
        self.state_file = os.getenv(
            "STATE_FILE",
            ".telnyx_adapter_state.json"
        )

        # Telnyx API credentials
        self.telnyx_api_key = os.getenv("TELNYX_API_KEY")
        self.telnyx_api_secret = os.getenv("TELNYX_API_SECRET")

        # Telnyx API base URL
        self.telnyx_api_url = os.getenv(
            "TELNYX_API_URL",
            "https://api.telnyx.com/v2"
        )

        # Webhook public key for signature validation
        self.telnyx_public_key = os.getenv("TELNYX_PUBLIC_KEY")

        # Whether to validate webhook signatures
        self.validate_webhook = os.getenv(
            "VALIDATE_TELNYX_WEBHOOK",
            "true"
        ).lower() in ("true", "1", "yes")

        # Webhook URL (for signature validation)
        self.webhook_url = os.getenv("TELNYX_WEBHOOK_URL")

    def get_api_headers(self) -> dict:
        """Get headers for Telnyx API requests.

        Returns:
            Dictionary of HTTP headers
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.telnyx_api_key:
            headers["Authorization"] = f"Bearer {self.telnyx_api_key}"
        return headers
