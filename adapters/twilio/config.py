"""Twilio-specific configuration extending base config."""

import os

from core.base_config import BaseConfig


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("true", "1", "yes")


class TwilioConfig(BaseConfig):
    """Twilio adapter configuration with platform-specific settings."""

    def __init__(self, env_file: str | None = None):
        super().__init__(env_file)

        self.state_file = os.getenv("STATE_FILE", ".twilio_adapter_state.json")

        # Twilio credentials
        self.twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.validate_twilio_signature = _env_bool("VALIDATE_TWILIO_SIGNATURE", "true")

        if self.validate_twilio_signature and not self.twilio_auth_token:
            raise ValueError("TWILIO_AUTH_TOKEN is required when VALIDATE_TWILIO_SIGNATURE is true")

        self.webhook_url = os.getenv("WEBHOOK_URL")

        # Per-mode feature toggles
        self.enable_voice_recording = _env_bool("TWILIO_ENABLE_VOICE_RECORDING", "true")
        self.enable_voice_status = _env_bool("TWILIO_ENABLE_VOICE_STATUS", "true")
        self.enable_messaging = _env_bool("TWILIO_ENABLE_MESSAGING", "true")
        self.enable_fax = _env_bool("TWILIO_ENABLE_FAX", "true")
        self.enable_video = _env_bool("TWILIO_ENABLE_VIDEO", "true")
        self.enable_conversations = _env_bool("TWILIO_ENABLE_CONVERSATIONS", "true")

        # Media download toggles
        self.download_messaging_media = _env_bool("DOWNLOAD_MESSAGING_MEDIA", "true")
        self.download_fax = _env_bool("DOWNLOAD_FAX", "true")
        self.download_video = _env_bool("DOWNLOAD_VIDEO", "true")

        # Messaging session window (hours) for future session aggregation
        self.messaging_session_window_hours = int(
            os.getenv("MESSAGING_SESSION_WINDOW_HOURS", "24")
        )

        # Webhook path overrides (optional)
        self.messaging_webhook_path = os.getenv("MESSAGING_WEBHOOK_PATH", "/webhook/messaging")

    def get_twilio_auth(self) -> tuple | None:
        if self.twilio_account_sid and self.twilio_auth_token:
            return (self.twilio_account_sid, self.twilio_auth_token)
        return None
