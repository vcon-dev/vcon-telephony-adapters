"""Shared pytest fixtures for Twilio adapter tests."""

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from twilio_adapter.config import Config
from twilio_adapter.builder import TwilioRecordingData, VconBuilder
from twilio_adapter.poster import HttpPoster
from twilio_adapter.tracker import StateTracker
from twilio_adapter.webhook import create_app


# =============================================================================
# Environment Fixtures
# =============================================================================

@pytest.fixture
def clean_env(monkeypatch):
    """Clean environment of all adapter-related variables."""
    env_vars = [
        "CONSERVER_URL", "CONSERVER_API_TOKEN", "CONSERVER_HEADER_NAME",
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "VALIDATE_TWILIO_SIGNATURE",
        "WEBHOOK_URL", "HOST", "PORT", "LOG_LEVEL",
        "DOWNLOAD_RECORDINGS", "RECORDING_FORMAT",
        "STATE_FILE", "INGRESS_LISTS",
    ]
    for var in env_vars:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def minimal_env(clean_env):
    """Minimal environment for Config to load."""
    clean_env.setenv("CONSERVER_URL", "https://example.com/vcons")
    clean_env.setenv("VALIDATE_TWILIO_SIGNATURE", "false")
    return clean_env


@pytest.fixture
def full_env(clean_env):
    """Full environment with all settings."""
    clean_env.setenv("CONSERVER_URL", "https://example.com/vcons")
    clean_env.setenv("CONSERVER_API_TOKEN", "test_api_token")
    clean_env.setenv("CONSERVER_HEADER_NAME", "x-custom-token")
    clean_env.setenv("TWILIO_ACCOUNT_SID", "AC1234567890abcdef")
    clean_env.setenv("TWILIO_AUTH_TOKEN", "auth_token_12345")
    clean_env.setenv("VALIDATE_TWILIO_SIGNATURE", "true")
    clean_env.setenv("WEBHOOK_URL", "https://webhook.example.com/recording")
    clean_env.setenv("HOST", "127.0.0.1")
    clean_env.setenv("PORT", "9000")
    clean_env.setenv("LOG_LEVEL", "DEBUG")
    clean_env.setenv("DOWNLOAD_RECORDINGS", "true")
    clean_env.setenv("RECORDING_FORMAT", "mp3")
    clean_env.setenv("INGRESS_LISTS", "list1,list2,list3")
    return clean_env


# =============================================================================
# Config Fixtures
# =============================================================================

@pytest.fixture
def minimal_config(minimal_env):
    """Config with minimal settings."""
    return Config()


@pytest.fixture
def full_config(full_env):
    """Config with all settings."""
    return Config()


# =============================================================================
# Twilio Webhook Data Fixtures
# =============================================================================

@pytest.fixture
def basic_webhook_data() -> Dict[str, Any]:
    """Basic Twilio recording webhook data."""
    # NOTE: These are intentionally fake test values, not real credentials
    return {
        "RecordingSid": "REtest1234567890abcdef12345678",
        "AccountSid": "ACtest1234567890abcdef12345678",
        "CallSid": "CAtest1234567890abcdef12345678",
        "RecordingUrl": "https://api.twilio.com/2010-04-01/Accounts/ACtest123/Recordings/REtest123",
        "RecordingStatus": "completed",
        "RecordingDuration": "120",
        "RecordingChannels": "1",
        "RecordingSource": "RecordVerb",
        "From": "+15551234567",
        "To": "+15559876543",
        "Direction": "inbound",
        "CallStatus": "completed",
        "ApiVersion": "2010-04-01",
    }


@pytest.fixture
def full_webhook_data(basic_webhook_data) -> Dict[str, Any]:
    """Full Twilio recording webhook data with all fields."""
    return {
        **basic_webhook_data,
        "RecordingStartTime": "Tue, 21 Jan 2025 10:30:00 +0000",
        "Caller": "+15551234567",
        "Called": "+15559876543",
        "ForwardedFrom": "+15550001111",
        "CallerCity": "San Francisco",
        "CallerState": "CA",
        "CallerZip": "94102",
        "CallerCountry": "US",
        "CalledCity": "New York",
        "CalledState": "NY",
        "CalledZip": "10001",
        "CalledCountry": "US",
    }


@pytest.fixture
def outbound_webhook_data(basic_webhook_data) -> Dict[str, Any]:
    """Webhook data for outbound call."""
    return {
        **basic_webhook_data,
        "Direction": "outbound-api",
    }


@pytest.fixture
def minimal_webhook_data() -> Dict[str, Any]:
    """Minimal webhook data with only required fields."""
    return {
        "RecordingSid": "RE_MINIMAL",
        "RecordingStatus": "completed",
        "From": "+15551111111",
        "To": "+15552222222",
    }


@pytest.fixture
def in_progress_webhook_data(basic_webhook_data) -> Dict[str, Any]:
    """Webhook data for in-progress recording."""
    return {
        **basic_webhook_data,
        "RecordingStatus": "in-progress",
    }


@pytest.fixture
def failed_webhook_data(basic_webhook_data) -> Dict[str, Any]:
    """Webhook data for failed recording."""
    return {
        **basic_webhook_data,
        "RecordingStatus": "failed",
    }


@pytest.fixture
def absent_webhook_data(basic_webhook_data) -> Dict[str, Any]:
    """Webhook data for absent recording."""
    return {
        **basic_webhook_data,
        "RecordingStatus": "absent",
    }


# =============================================================================
# Recording Data Fixtures
# =============================================================================

@pytest.fixture
def basic_recording_data(basic_webhook_data) -> TwilioRecordingData:
    """TwilioRecordingData from basic webhook."""
    return TwilioRecordingData(basic_webhook_data)


@pytest.fixture
def full_recording_data(full_webhook_data) -> TwilioRecordingData:
    """TwilioRecordingData with all fields."""
    return TwilioRecordingData(full_webhook_data)


# =============================================================================
# Builder Fixtures
# =============================================================================

@pytest.fixture
def builder_no_download() -> VconBuilder:
    """VconBuilder that doesn't download recordings."""
    return VconBuilder(download_recordings=False)


@pytest.fixture
def builder_mp3() -> VconBuilder:
    """VconBuilder configured for MP3 format."""
    return VconBuilder(download_recordings=False, recording_format="mp3")


@pytest.fixture
def builder_with_auth() -> VconBuilder:
    """VconBuilder with Twilio auth configured."""
    return VconBuilder(
        download_recordings=True,
        recording_format="wav",
        twilio_auth=("AC123", "auth_token")
    )


# =============================================================================
# Poster Fixtures
# =============================================================================

@pytest.fixture
def basic_poster() -> HttpPoster:
    """Basic HttpPoster without ingress lists."""
    return HttpPoster(
        url="https://example.com/vcons",
        headers={"Content-Type": "application/json"}
    )


@pytest.fixture
def poster_with_auth() -> HttpPoster:
    """HttpPoster with authentication headers."""
    return HttpPoster(
        url="https://example.com/vcons",
        headers={
            "Content-Type": "application/json",
            "x-conserver-api-token": "test_token"
        }
    )


@pytest.fixture
def poster_with_ingress() -> HttpPoster:
    """HttpPoster with ingress lists configured."""
    return HttpPoster(
        url="https://example.com/vcons",
        headers={"Content-Type": "application/json"},
        ingress_lists=["transcription", "analysis", "storage"]
    )


# =============================================================================
# Tracker Fixtures
# =============================================================================

@pytest.fixture
def temp_state_file():
    """Create a temporary state file and clean up after."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    yield path
    if Path(path).exists():
        Path(path).unlink()


@pytest.fixture
def tracker(temp_state_file) -> StateTracker:
    """StateTracker with temporary state file."""
    return StateTracker(temp_state_file)


@pytest.fixture
def preloaded_tracker(temp_state_file) -> StateTracker:
    """StateTracker with pre-existing processed recordings."""
    tracker = StateTracker(temp_state_file)
    tracker.mark_processed("RE_EXISTING_1", "vcon-uuid-1", status="success")
    tracker.mark_processed("RE_EXISTING_2", "vcon-uuid-2", status="success")
    tracker.mark_processed("RE_FAILED", "vcon-uuid-3", status="failed")
    return tracker


# =============================================================================
# Webhook/API Fixtures
# =============================================================================

@pytest.fixture
def mock_config(minimal_env):
    """Config for webhook testing."""
    minimal_env.setenv("DOWNLOAD_RECORDINGS", "false")
    return Config()


@pytest.fixture
def test_client(mock_config):
    """FastAPI test client."""
    app = create_app(mock_config)
    return TestClient(app)


@pytest.fixture
def mock_poster():
    """Mock HttpPoster that always succeeds."""
    poster = MagicMock(spec=HttpPoster)
    poster.post.return_value = True
    return poster


@pytest.fixture
def mock_failing_poster():
    """Mock HttpPoster that always fails."""
    poster = MagicMock(spec=HttpPoster)
    poster.post.return_value = False
    return poster


# =============================================================================
# Sample Audio Data Fixtures
# =============================================================================

@pytest.fixture
def sample_audio_bytes() -> bytes:
    """Sample audio data (fake WAV header + data)."""
    # Minimal WAV file header
    return (
        b'RIFF'
        b'\x24\x00\x00\x00'  # File size
        b'WAVE'
        b'fmt '
        b'\x10\x00\x00\x00'  # Chunk size
        b'\x01\x00'          # Audio format (PCM)
        b'\x01\x00'          # Num channels
        b'\x44\xac\x00\x00'  # Sample rate (44100)
        b'\x88\x58\x01\x00'  # Byte rate
        b'\x02\x00'          # Block align
        b'\x10\x00'          # Bits per sample
        b'data'
        b'\x00\x00\x00\x00'  # Data size
    )


# =============================================================================
# Utility Functions
# =============================================================================

def create_mock_response(status_code: int, content: bytes = b"", text: str = ""):
    """Create a mock requests.Response object."""
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    response.text = text or content.decode("utf-8", errors="replace")
    return response
