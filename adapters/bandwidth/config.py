"""Configuration management for Bandwidth adapter."""

import logging
import os

from core.base_config import BaseConfig

logger = logging.getLogger(__name__)


class BandwidthConfig(BaseConfig):
    """Bandwidth-specific configuration.

    Extends BaseConfig with Bandwidth-specific settings for
    API authentication and webhook validation.
    """

    def __init__(self, env_file: str | None = None):
        """Load configuration from environment.

        Args:
            env_file: Optional path to .env file
        """
        super().__init__(env_file)

        # State file specific to Bandwidth
        self.state_file = os.getenv("STATE_FILE", ".bandwidth_adapter_state.json")

        # Bandwidth API credentials
        self.bandwidth_account_id = os.getenv("BANDWIDTH_ACCOUNT_ID")
        self.bandwidth_username = os.getenv("BANDWIDTH_USERNAME")
        self.bandwidth_password = os.getenv("BANDWIDTH_PASSWORD")

        # Bandwidth API URLs
        self.bandwidth_voice_api_url = os.getenv(
            "BANDWIDTH_VOICE_API_URL", "https://voice.bandwidth.com/api/v2"
        )

        # Application ID for voice calls
        self.bandwidth_application_id = os.getenv("BANDWIDTH_APPLICATION_ID")

        # Webhook authentication (basic auth credentials, required unless
        # ALLOW_UNSIGNED_WEBHOOKS is set)
        self.webhook_username = os.getenv("BANDWIDTH_WEBHOOK_USERNAME")
        self.webhook_password = os.getenv("BANDWIDTH_WEBHOOK_PASSWORD")

        # Whether to validate webhook authentication
        self.validate_webhook = os.getenv("VALIDATE_BANDWIDTH_WEBHOOK", "true").lower() in (
            "true",
            "1",
            "yes",
        )

        if self.validate_webhook and not (self.webhook_username and self.webhook_password):
            if self.allow_unsigned_webhooks:
                logger.warning(
                    "VALIDATE_BANDWIDTH_WEBHOOK is enabled but BANDWIDTH_WEBHOOK_USERNAME/"
                    "BANDWIDTH_WEBHOOK_PASSWORD are not both set; accepting unsigned "
                    "webhooks because ALLOW_UNSIGNED_WEBHOOKS=true"
                )
            else:
                raise ValueError(
                    "BANDWIDTH_WEBHOOK_USERNAME and BANDWIDTH_WEBHOOK_PASSWORD are required "
                    "when VALIDATE_BANDWIDTH_WEBHOOK is true. Set both, or set "
                    "ALLOW_UNSIGNED_WEBHOOKS=true to accept unsigned webhooks (not "
                    "recommended)."
                )

    def get_api_auth(self) -> tuple:
        """Get API authentication tuple.

        Returns:
            Tuple of (username, password) for Bandwidth API requests
        """
        return (self.bandwidth_username, self.bandwidth_password)

    def get_api_headers(self) -> dict:
        """Get headers for Bandwidth API requests.

        Returns:
            Dictionary of HTTP headers
        """
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
