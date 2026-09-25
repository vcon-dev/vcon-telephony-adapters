"""Configuration management for Telnyx adapter."""

import logging
import os

from core.base_config import BaseConfig

logger = logging.getLogger(__name__)


class TelnyxConfig(BaseConfig):
    """Telnyx-specific configuration.

    Extends BaseConfig with Telnyx-specific settings for
    API authentication and webhook validation.
    """

    def __init__(self, env_file: str | None = None):
        """Load configuration from environment.

        Args:
            env_file: Optional path to .env file
        """
        super().__init__(env_file)

        # State file specific to Telnyx
        self.state_file = os.getenv("STATE_FILE", ".telnyx_adapter_state.json")

        # Telnyx API credentials
        self.telnyx_api_key = os.getenv("TELNYX_API_KEY")
        self.telnyx_api_secret = os.getenv("TELNYX_API_SECRET")

        # Telnyx API base URL
        self.telnyx_api_url = os.getenv("TELNYX_API_URL", "https://api.telnyx.com/v2")

        # Webhook public key for signature validation (required unless
        # ALLOW_UNSIGNED_WEBHOOKS is set)
        self.telnyx_public_key = os.getenv("TELNYX_PUBLIC_KEY")

        # Whether to validate webhook signatures
        self.validate_webhook = os.getenv("VALIDATE_TELNYX_WEBHOOK", "true").lower() in (
            "true",
            "1",
            "yes",
        )

        # Webhook URL (for signature validation)
        self.webhook_url = os.getenv("TELNYX_WEBHOOK_URL")

        # Shared-secret token for /webhook/texml-recording. Telnyx's TeXML
        # recordingStatusCallback is a Twilio-compatible form POST with no
        # ed25519 signature of its own (unlike Call Control webhooks), so it
        # is gated with a token in the callback URL instead: configure the
        # recordingStatusCallback as
        # https://<host>/webhook/texml-recording?token=<this value>.
        self.texml_callback_token = os.getenv("TELNYX_TEXML_CALLBACK_TOKEN")

        if self.validate_webhook and not self.telnyx_public_key:
            if self.allow_unsigned_webhooks:
                logger.warning(
                    "VALIDATE_TELNYX_WEBHOOK is enabled but TELNYX_PUBLIC_KEY is not set; "
                    "accepting unsigned Call Control webhooks because "
                    "ALLOW_UNSIGNED_WEBHOOKS=true"
                )
            else:
                raise ValueError(
                    "TELNYX_PUBLIC_KEY is required when VALIDATE_TELNYX_WEBHOOK is true. "
                    "Set the key, or set ALLOW_UNSIGNED_WEBHOOKS=true to accept unsigned "
                    "webhooks (not recommended)."
                )

        if self.validate_webhook and not self.texml_callback_token:
            if self.allow_unsigned_webhooks:
                logger.warning(
                    "VALIDATE_TELNYX_WEBHOOK is enabled but TELNYX_TEXML_CALLBACK_TOKEN is "
                    "not set; /webhook/texml-recording will accept unauthenticated callbacks "
                    "because ALLOW_UNSIGNED_WEBHOOKS=true"
                )
            else:
                raise ValueError(
                    "TELNYX_TEXML_CALLBACK_TOKEN is required when VALIDATE_TELNYX_WEBHOOK is "
                    "true, to protect /webhook/texml-recording (TeXML callbacks are not "
                    "ed25519-signed). Set the token, or set ALLOW_UNSIGNED_WEBHOOKS=true to "
                    "accept unsigned webhooks (not recommended)."
                )

        if self.validate_webhook and not self.allow_unsigned_webhooks:
            try:
                import cryptography  # noqa: F401
            except ImportError:
                raise ValueError(
                    "VALIDATE_TELNYX_WEBHOOK is true but the 'cryptography' package is not "
                    "installed, so Call Control webhook signatures cannot be verified. "
                    "Install cryptography, or set ALLOW_UNSIGNED_WEBHOOKS=true to accept "
                    "unsigned webhooks (not recommended)."
                ) from None

        # --- Smart trunk (bring your own key) ---
        # When auto_siprec is on, every answered call is forked to our SRS via
        # the customer's own Telnyx account. Off by default: forking someone's
        # calls is not something to start doing because a variable was unset.
        self.auto_siprec = os.getenv("TELNYX_AUTO_SIPREC", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        self.siprec_connector_name = os.getenv("TELNYX_CONNECTOR_NAME", "vconic-smart-trunk")
        # Telnyx-side realtime transcription, billed per minute to the customer.
        self.transcribe_realtime = os.getenv("TELNYX_REALTIME_TRANSCRIPTION", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        # Smart trunk live layer: stream every answered call to the vcon-realtime
        # bridge over a WebSocket. Sibling of auto_siprec. Telnyx allows only one
        # stream-or-fork per call, so enable one or the other, not both. Off by
        # default for the same reason forking is. stream_url is the bridge's public
        # wss endpoint (e.g. wss://host/telnyx/media).
        self.auto_stream = os.getenv("TELNYX_AUTO_STREAM", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        self.stream_url = os.getenv("TELNYX_STREAM_URL", "")
        self.stream_track = os.getenv("TELNYX_STREAM_TRACK", "both_tracks")

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
