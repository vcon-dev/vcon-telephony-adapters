"""Comprehensive tests for configuration module."""

import os
import tempfile
from pathlib import Path

import pytest

from twilio_adapter.config import Config


class TestConfigRequired:
    """Tests for required configuration values."""

    def test_requires_conserver_url(self, clean_env):
        """CONSERVER_URL is required."""
        clean_env.setenv("VALIDATE_TWILIO_SIGNATURE", "false")

        with pytest.raises(ValueError, match="CONSERVER_URL"):
            Config()

    def test_requires_auth_token_when_validating_signatures(self, clean_env):
        """TWILIO_AUTH_TOKEN required when signature validation enabled."""
        clean_env.setenv("CONSERVER_URL", "https://example.com/vcons")
        clean_env.setenv("VALIDATE_TWILIO_SIGNATURE", "true")

        with pytest.raises(ValueError, match="TWILIO_AUTH_TOKEN"):
            Config()

    def test_allows_missing_auth_token_when_validation_disabled(self, clean_env):
        """Auth token not required when signature validation disabled."""
        clean_env.setenv("CONSERVER_URL", "https://example.com/vcons")
        clean_env.setenv("VALIDATE_TWILIO_SIGNATURE", "false")

        config = Config()
        assert config.twilio_auth_token is None

    def test_accepts_valid_minimal_config(self, minimal_env):
        """Minimal valid configuration loads successfully."""
        config = Config()
        assert config.conserver_url == "https://example.com/vcons"


class TestConfigDefaults:
    """Tests for default configuration values."""

    def test_host_default(self, minimal_config):
        """Host defaults to 0.0.0.0."""
        assert minimal_config.host == "0.0.0.0"

    def test_port_default(self, minimal_config):
        """Port defaults to 8080."""
        assert minimal_config.port == 8080

    def test_recording_format_default(self, minimal_config):
        """Recording format defaults to wav."""
        assert minimal_config.recording_format == "wav"

    def test_download_recordings_default(self, minimal_config):
        """Download recordings defaults to True."""
        assert minimal_config.download_recordings is True

    def test_log_level_default(self, minimal_config):
        """Log level defaults to INFO."""
        assert minimal_config.log_level == "INFO"

    def test_validate_signature_default_true(self, clean_env):
        """Signature validation defaults to true (requires token)."""
        clean_env.setenv("CONSERVER_URL", "https://example.com/vcons")
        clean_env.setenv("TWILIO_AUTH_TOKEN", "token")

        config = Config()
        assert config.validate_twilio_signature is True

    def test_state_file_default(self, minimal_config):
        """State file has default path."""
        assert minimal_config.state_file == ".twilio_adapter_state.json"

    def test_ingress_lists_default_empty(self, minimal_config):
        """Ingress lists defaults to empty list."""
        assert minimal_config.ingress_lists == []

    def test_conserver_header_name_default(self, minimal_config):
        """Conserver header name has default value."""
        assert minimal_config.conserver_header_name == "x-conserver-api-token"

    def test_webhook_url_default_none(self, minimal_config):
        """Webhook URL defaults to None."""
        assert minimal_config.webhook_url is None


class TestConfigCustomValues:
    """Tests for custom configuration values."""

    def test_custom_host(self, minimal_env):
        """Custom host is respected."""
        minimal_env.setenv("HOST", "192.168.1.100")
        config = Config()
        assert config.host == "192.168.1.100"

    def test_custom_port(self, minimal_env):
        """Custom port is respected."""
        minimal_env.setenv("PORT", "3000")
        config = Config()
        assert config.port == 3000

    def test_custom_port_as_string(self, minimal_env):
        """Port string is converted to int."""
        minimal_env.setenv("PORT", "8443")
        config = Config()
        assert config.port == 8443
        assert isinstance(config.port, int)

    def test_custom_log_level(self, minimal_env):
        """Custom log level is respected."""
        minimal_env.setenv("LOG_LEVEL", "debug")
        config = Config()
        assert config.log_level == "DEBUG"

    def test_custom_state_file(self, minimal_env):
        """Custom state file path is respected."""
        minimal_env.setenv("STATE_FILE", "/var/lib/adapter/state.json")
        config = Config()
        assert config.state_file == "/var/lib/adapter/state.json"

    def test_custom_conserver_header(self, minimal_env):
        """Custom conserver header name is respected."""
        minimal_env.setenv("CONSERVER_HEADER_NAME", "Authorization")
        config = Config()
        assert config.conserver_header_name == "Authorization"

    def test_custom_webhook_url(self, minimal_env):
        """Custom webhook URL is respected."""
        minimal_env.setenv("WEBHOOK_URL", "https://my-domain.com/webhook")
        config = Config()
        assert config.webhook_url == "https://my-domain.com/webhook"


class TestConfigRecordingFormat:
    """Tests for recording format configuration."""

    def test_wav_format(self, minimal_env):
        """WAV format is valid."""
        minimal_env.setenv("RECORDING_FORMAT", "wav")
        config = Config()
        assert config.recording_format == "wav"

    def test_mp3_format(self, minimal_env):
        """MP3 format is valid."""
        minimal_env.setenv("RECORDING_FORMAT", "mp3")
        config = Config()
        assert config.recording_format == "mp3"

    def test_format_case_insensitive(self, minimal_env):
        """Recording format is case insensitive."""
        minimal_env.setenv("RECORDING_FORMAT", "WAV")
        config = Config()
        assert config.recording_format == "wav"

        minimal_env.setenv("RECORDING_FORMAT", "Mp3")
        config = Config()
        assert config.recording_format == "mp3"

    def test_invalid_format_raises_error(self, minimal_env):
        """Invalid recording format raises ValueError."""
        minimal_env.setenv("RECORDING_FORMAT", "ogg")
        with pytest.raises(ValueError, match="RECORDING_FORMAT"):
            Config()

    def test_invalid_format_flac(self, minimal_env):
        """FLAC format is not supported."""
        minimal_env.setenv("RECORDING_FORMAT", "flac")
        with pytest.raises(ValueError, match="RECORDING_FORMAT"):
            Config()


class TestConfigBooleanParsing:
    """Tests for boolean configuration parsing."""

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "Yes", "YES"])
    def test_truthy_values_for_download_recordings(self, minimal_env, value):
        """Various truthy values are recognized."""
        minimal_env.setenv("DOWNLOAD_RECORDINGS", value)
        config = Config()
        assert config.download_recordings is True

    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no", "No", "NO", ""])
    def test_falsy_values_for_download_recordings(self, minimal_env, value):
        """Various falsy values are recognized."""
        minimal_env.setenv("DOWNLOAD_RECORDINGS", value)
        config = Config()
        assert config.download_recordings is False

    @pytest.mark.parametrize("value", ["true", "1", "yes"])
    def test_truthy_values_for_validate_signature(self, minimal_env, value):
        """Signature validation truthy values."""
        minimal_env.setenv("VALIDATE_TWILIO_SIGNATURE", value)
        minimal_env.setenv("TWILIO_AUTH_TOKEN", "token")
        config = Config()
        assert config.validate_twilio_signature is True

    @pytest.mark.parametrize("value", ["false", "0", "no"])
    def test_falsy_values_for_validate_signature(self, minimal_env, value):
        """Signature validation falsy values."""
        minimal_env.setenv("VALIDATE_TWILIO_SIGNATURE", value)
        config = Config()
        assert config.validate_twilio_signature is False


class TestConfigIngressLists:
    """Tests for ingress lists configuration."""

    def test_single_ingress_list(self, minimal_env):
        """Single ingress list is parsed correctly."""
        minimal_env.setenv("INGRESS_LISTS", "transcription")
        config = Config()
        assert config.ingress_lists == ["transcription"]

    def test_multiple_ingress_lists(self, minimal_env):
        """Multiple ingress lists are parsed correctly."""
        minimal_env.setenv("INGRESS_LISTS", "list1,list2,list3")
        config = Config()
        assert config.ingress_lists == ["list1", "list2", "list3"]

    def test_ingress_lists_with_spaces(self, minimal_env):
        """Whitespace is trimmed from ingress lists."""
        minimal_env.setenv("INGRESS_LISTS", " list1 , list2 , list3 ")
        config = Config()
        assert config.ingress_lists == ["list1", "list2", "list3"]

    def test_empty_ingress_lists(self, minimal_env):
        """Empty string results in empty list."""
        minimal_env.setenv("INGRESS_LISTS", "")
        config = Config()
        assert config.ingress_lists == []

    def test_ingress_lists_with_empty_elements(self, minimal_env):
        """Empty elements are filtered out."""
        minimal_env.setenv("INGRESS_LISTS", "list1,,list2,,,list3")
        config = Config()
        assert config.ingress_lists == ["list1", "list2", "list3"]


class TestConfigGetHeaders:
    """Tests for get_headers() method."""

    def test_headers_without_api_token(self, minimal_config):
        """Headers without API token."""
        headers = minimal_config.get_headers()
        assert headers == {"Content-Type": "application/json"}

    def test_headers_with_api_token(self, minimal_env):
        """Headers include API token when configured."""
        minimal_env.setenv("CONSERVER_API_TOKEN", "my_secret_token")
        config = Config()
        headers = config.get_headers()

        assert headers == {
            "Content-Type": "application/json",
            "x-conserver-api-token": "my_secret_token"
        }

    def test_headers_with_custom_header_name(self, minimal_env):
        """Headers use custom header name."""
        minimal_env.setenv("CONSERVER_API_TOKEN", "token123")
        minimal_env.setenv("CONSERVER_HEADER_NAME", "X-API-Key")
        config = Config()
        headers = config.get_headers()

        assert headers == {
            "Content-Type": "application/json",
            "X-API-Key": "token123"
        }


class TestConfigGetTwilioAuth:
    """Tests for get_twilio_auth() method."""

    def test_returns_none_without_credentials(self, minimal_config):
        """Returns None when credentials not configured."""
        auth = minimal_config.get_twilio_auth()
        assert auth is None

    def test_returns_none_with_partial_credentials(self, minimal_env):
        """Returns None with only account SID."""
        minimal_env.setenv("TWILIO_ACCOUNT_SID", "AC123")
        config = Config()
        auth = config.get_twilio_auth()
        assert auth is None

    def test_returns_tuple_with_full_credentials(self, minimal_env):
        """Returns tuple with both credentials."""
        minimal_env.setenv("TWILIO_ACCOUNT_SID", "AC1234567890")
        minimal_env.setenv("TWILIO_AUTH_TOKEN", "auth_token_abc")
        config = Config()
        auth = config.get_twilio_auth()

        assert auth == ("AC1234567890", "auth_token_abc")

    def test_returns_tuple_type(self, minimal_env):
        """Return value is a tuple."""
        minimal_env.setenv("TWILIO_ACCOUNT_SID", "AC123")
        minimal_env.setenv("TWILIO_AUTH_TOKEN", "token")
        config = Config()
        auth = config.get_twilio_auth()

        assert isinstance(auth, tuple)
        assert len(auth) == 2


class TestConfigFromEnvFile:
    """Tests for loading configuration from .env file."""

    def test_loads_from_env_file(self, clean_env):
        """Configuration loads from .env file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("CONSERVER_URL=https://from-file.example.com/vcons\n")
            f.write("VALIDATE_TWILIO_SIGNATURE=false\n")
            f.write("PORT=7777\n")
            env_file = f.name

        try:
            config = Config(env_file=env_file)
            assert config.conserver_url == "https://from-file.example.com/vcons"
            assert config.port == 7777
        finally:
            Path(env_file).unlink()

    def test_env_variables_override_file(self, clean_env):
        """Environment variables override .env file values."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("CONSERVER_URL=https://from-file.example.com/vcons\n")
            f.write("VALIDATE_TWILIO_SIGNATURE=false\n")
            f.write("PORT=7777\n")
            env_file = f.name

        try:
            # Set env var that should override file
            clean_env.setenv("PORT", "9999")
            config = Config(env_file=env_file)
            assert config.port == 9999
        finally:
            Path(env_file).unlink()


class TestConfigFullConfiguration:
    """Tests for full configuration with all options."""

    def test_full_configuration(self, full_config):
        """All configuration options are loaded correctly."""
        assert full_config.conserver_url == "https://example.com/vcons"
        assert full_config.conserver_api_token == "test_api_token"
        assert full_config.conserver_header_name == "x-custom-token"
        assert full_config.twilio_account_sid == "AC1234567890abcdef"
        assert full_config.twilio_auth_token == "auth_token_12345"
        assert full_config.validate_twilio_signature is True
        assert full_config.webhook_url == "https://webhook.example.com/recording"
        assert full_config.host == "127.0.0.1"
        assert full_config.port == 9000
        assert full_config.log_level == "DEBUG"
        assert full_config.download_recordings is True
        assert full_config.recording_format == "mp3"
        assert full_config.ingress_lists == ["list1", "list2", "list3"]

    def test_full_config_headers(self, full_config):
        """Full config produces correct headers."""
        headers = full_config.get_headers()
        assert headers == {
            "Content-Type": "application/json",
            "x-custom-token": "test_api_token"
        }

    def test_full_config_twilio_auth(self, full_config):
        """Full config produces correct Twilio auth."""
        auth = full_config.get_twilio_auth()
        assert auth == ("AC1234567890abcdef", "auth_token_12345")
