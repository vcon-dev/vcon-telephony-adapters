"""Configuration management for Twilio adapter using .env file."""

import os
from typing import Dict, List, Optional
from dotenv import load_dotenv


class Config:
    """Manages configuration from environment variables."""

    def __init__(self, env_file: Optional[str] = None):
        """Load configuration from .env file."""
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        # Server settings
        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = int(os.getenv("PORT", "8080"))

        # Twilio settings (for signature validation)
        self.twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.validate_twilio_signature = os.getenv(
            "VALIDATE_TWILIO_SIGNATURE", "true"
        ).lower() in ("true", "1", "yes")

        if self.validate_twilio_signature and not self.twilio_auth_token:
            raise ValueError(
                "TWILIO_AUTH_TOKEN is required when VALIDATE_TWILIO_SIGNATURE is true"
            )

        # Webhook URL for signature validation (optional, can be auto-detected)
        self.webhook_url = os.getenv("WEBHOOK_URL")

        # Conserver settings (required)
        self.conserver_url = os.getenv("CONSERVER_URL")
        if not self.conserver_url:
            raise ValueError("CONSERVER_URL environment variable is required")

        # Optional conserver authentication
        self.conserver_api_token = os.getenv("CONSERVER_API_TOKEN")
        self.conserver_header_name = os.getenv(
            "CONSERVER_HEADER_NAME",
            "x-conserver-api-token"
        )

        # State tracking
        self.state_file = os.getenv("STATE_FILE", ".twilio_adapter_state.json")

        # Ingress lists for vCon routing
        ingress_lists_str = os.getenv("INGRESS_LISTS", "")
        self.ingress_lists = [
            item.strip()
            for item in ingress_lists_str.split(",")
            if item.strip()
        ]

        # Recording download settings
        self.download_recordings = os.getenv(
            "DOWNLOAD_RECORDINGS", "true"
        ).lower() in ("true", "1", "yes")

        # Recording format preference (wav or mp3)
        self.recording_format = os.getenv("RECORDING_FORMAT", "wav").lower()
        if self.recording_format not in ("wav", "mp3"):
            raise ValueError(
                f"RECORDING_FORMAT must be 'wav' or 'mp3', got: {self.recording_format}"
            )

        # Logging level
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    def get_headers(self) -> Dict[str, str]:
        """Get HTTP headers for conserver requests."""
        headers = {"Content-Type": "application/json"}
        if self.conserver_api_token:
            headers[self.conserver_header_name] = self.conserver_api_token
        return headers

    def get_twilio_auth(self) -> Optional[tuple]:
        """Get Twilio authentication tuple for API requests."""
        if self.twilio_account_sid and self.twilio_auth_token:
            return (self.twilio_account_sid, self.twilio_auth_token)
        return None
