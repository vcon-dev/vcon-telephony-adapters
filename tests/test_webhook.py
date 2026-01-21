"""Comprehensive tests for webhook module."""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from twilio_adapter.config import Config
from twilio_adapter.webhook import create_app


def unique_recording_sid(prefix: str = "RE_TEST") -> str:
    """Generate a unique recording SID for testing."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# =============================================================================
# Health Endpoint Tests
# =============================================================================

class TestHealthEndpoint:
    """Tests for health check endpoint."""

    def test_health_check_returns_200(self, test_client):
        """Health check returns 200 status."""
        response = test_client.get("/health")
        assert response.status_code == 200

    def test_health_check_returns_healthy_status(self, test_client):
        """Health check returns healthy status."""
        response = test_client.get("/health")
        data = response.json()

        assert data["status"] == "healthy"

    def test_health_check_returns_service_name(self, test_client):
        """Health check returns service name."""
        response = test_client.get("/health")
        data = response.json()

        assert data["service"] == "vcon-twilio-adapter"

    def test_health_check_is_json(self, test_client):
        """Health check returns JSON content type."""
        response = test_client.get("/health")

        assert response.headers["content-type"] == "application/json"


# =============================================================================
# Recording Webhook - Basic Tests
# =============================================================================

class TestRecordingWebhookBasic:
    """Basic tests for recording webhook endpoint."""

    def test_returns_200_for_valid_request(self, test_client):
        """Returns 200 for valid webhook request."""
        response = test_client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE123",
                "RecordingStatus": "in-progress",
            }
        )

        assert response.status_code == 200

    def test_returns_ok_text(self, test_client):
        """Returns 'OK' text body."""
        response = test_client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE123",
                "RecordingStatus": "in-progress",
            }
        )

        assert response.text == "OK"

    def test_accepts_form_encoded_data(self, test_client):
        """Accepts form-encoded POST data."""
        response = test_client.post(
            "/webhook/recording",
            data={"RecordingSid": "RE123", "RecordingStatus": "completed"}
        )

        assert response.status_code == 200


# =============================================================================
# Recording Webhook - Status Filtering
# =============================================================================

class TestRecordingWebhookStatusFiltering:
    """Tests for recording status filtering."""

    def test_ignores_in_progress_status(self, test_client):
        """Ignores recordings with in-progress status."""
        response = test_client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE_INPROGRESS",
                "RecordingStatus": "in-progress",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        assert response.status_code == 200
        # Should not be tracked
        status_response = test_client.get("/status/RE_INPROGRESS")
        assert status_response.status_code == 404

    def test_ignores_failed_status(self, test_client):
        """Ignores recordings with failed status."""
        response = test_client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE_FAILED",
                "RecordingStatus": "failed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        assert response.status_code == 200

    def test_ignores_absent_status(self, test_client):
        """Ignores recordings with absent status."""
        response = test_client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE_ABSENT",
                "RecordingStatus": "absent",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        assert response.status_code == 200

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_processes_completed_status(self, mock_poster_class, mock_config):
        """Processes recordings with completed status."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        recording_sid = unique_recording_sid("RE_COMPLETED")
        response = client.post(
            "/webhook/recording",
            data={
                "RecordingSid": recording_sid,
                "RecordingStatus": "completed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        assert response.status_code == 200
        mock_poster.post.assert_called_once()


# =============================================================================
# Recording Webhook - Duplicate Prevention
# =============================================================================

class TestRecordingWebhookDuplicatePrevention:
    """Tests for duplicate recording prevention."""

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_skips_already_processed(self, mock_poster_class, mock_config):
        """Skips already processed recordings."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        # First request - should process
        client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE_DUPLICATE_TEST",
                "RecordingStatus": "completed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        # Reset mock to track second call
        mock_poster.post.reset_mock()

        # Second request - should skip
        client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE_DUPLICATE_TEST",
                "RecordingStatus": "completed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        # Should not have posted again
        mock_poster.post.assert_not_called()

    def test_returns_ok_for_duplicate(self, test_client):
        """Returns OK for duplicate requests."""
        with patch("twilio_adapter.webhook.HttpPoster") as mock_poster_class:
            mock_poster = MagicMock()
            mock_poster.post.return_value = True
            mock_poster_class.return_value = mock_poster

            # First request
            test_client.post(
                "/webhook/recording",
                data={
                    "RecordingSid": "RE_DUP_OK",
                    "RecordingStatus": "completed",
                    "From": "+15551234567",
                    "To": "+15559876543",
                }
            )

        # Second request (without mock, but already tracked)
        response = test_client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE_DUP_OK",
                "RecordingStatus": "completed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        assert response.status_code == 200
        assert response.text == "OK"


# =============================================================================
# Recording Webhook - Full Processing
# =============================================================================

class TestRecordingWebhookFullProcessing:
    """Tests for full recording processing flow."""

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_creates_vcon_from_webhook_data(self, mock_poster_class, mock_config):
        """Creates vCon from webhook data."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        recording_sid = unique_recording_sid("RE_VCON_TEST")
        client.post(
            "/webhook/recording",
            data={
                "RecordingSid": recording_sid,
                "RecordingStatus": "completed",
                "CallSid": "CA123",
                "AccountSid": "AC456",
                "RecordingUrl": "https://api.twilio.com/recording",
                "RecordingDuration": "60",
                "From": "+15551234567",
                "To": "+15559876543",
                "Direction": "inbound",
            }
        )

        # Verify vCon was posted
        mock_poster.post.assert_called_once()
        posted_vcon = mock_poster.post.call_args[0][0]

        # Verify vCon has correct parties (vcon lib uses dicts)
        assert len(posted_vcon.parties) == 2
        assert posted_vcon.parties[0]["tel"] == "+15551234567"
        assert posted_vcon.parties[1]["tel"] == "+15559876543"

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_tracks_success_status(self, mock_poster_class, mock_config):
        """Tracks success status after posting."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        recording_sid = unique_recording_sid("RE_SUCCESS_TRACK")
        client.post(
            "/webhook/recording",
            data={
                "RecordingSid": recording_sid,
                "RecordingStatus": "completed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        # Check status
        response = client.get(f"/status/{recording_sid}")
        data = response.json()

        assert data["status"] == "success"

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_tracks_failure_status(self, mock_poster_class, mock_config):
        """Tracks failure status when posting fails."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = False  # Simulate failure
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        recording_sid = unique_recording_sid("RE_FAILURE_TRACK")
        client.post(
            "/webhook/recording",
            data={
                "RecordingSid": recording_sid,
                "RecordingStatus": "completed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        # Check status
        response = client.get(f"/status/{recording_sid}")
        data = response.json()

        assert data["status"] == "post_failed"


# =============================================================================
# Recording Webhook - All Parameters
# =============================================================================

class TestRecordingWebhookAllParameters:
    """Tests for handling all webhook parameters."""

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_handles_all_parameters(self, mock_poster_class, mock_config):
        """Handles all Twilio webhook parameters."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        recording_sid = unique_recording_sid("RE_FULL_PARAMS")
        response = client.post(
            "/webhook/recording",
            data={
                "RecordingSid": recording_sid,
                "AccountSid": "AC123456",
                "CallSid": "CA789012",
                "RecordingUrl": "https://api.twilio.com/recording",
                "RecordingStatus": "completed",
                "RecordingDuration": "120",
                "RecordingChannels": "2",
                "RecordingSource": "RecordVerb",
                "RecordingStartTime": "Tue, 21 Jan 2025 10:30:00 +0000",
                "From": "+15551234567",
                "To": "+15559876543",
                "Caller": "+15551234567",
                "Called": "+15559876543",
                "Direction": "inbound",
                "CallStatus": "completed",
                "ApiVersion": "2010-04-01",
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
        )

        assert response.status_code == 200

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_handles_minimal_parameters(self, mock_poster_class, mock_config):
        """Handles minimal required parameters."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        recording_sid = unique_recording_sid("RE_MINIMAL_PARAMS")
        response = client.post(
            "/webhook/recording",
            data={
                "RecordingSid": recording_sid,
                "RecordingStatus": "completed",
            }
        )

        assert response.status_code == 200


# =============================================================================
# Status Endpoint Tests
# =============================================================================

class TestStatusEndpoint:
    """Tests for status endpoint."""

    def test_returns_404_for_unknown_recording(self, test_client):
        """Returns 404 for unknown recording."""
        response = test_client.get("/status/RE_UNKNOWN_12345")

        assert response.status_code == 404

    def test_returns_404_detail_message(self, test_client):
        """Returns detail message for 404."""
        response = test_client.get("/status/RE_NOT_FOUND")
        data = response.json()

        assert "detail" in data
        assert "not found" in data["detail"].lower()

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_returns_recording_sid(self, mock_poster_class, mock_config):
        """Returns recording SID in response."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        # Process recording first
        client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE_STATUS_SID",
                "RecordingStatus": "completed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        # Check status
        response = client.get("/status/RE_STATUS_SID")
        data = response.json()

        assert data["recording_sid"] == "RE_STATUS_SID"

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_returns_vcon_uuid(self, mock_poster_class, mock_config):
        """Returns vCon UUID in response."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        # Process recording first
        client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE_STATUS_UUID",
                "RecordingStatus": "completed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        # Check status
        response = client.get("/status/RE_STATUS_UUID")
        data = response.json()

        assert "vcon_uuid" in data
        assert data["vcon_uuid"] is not None

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_returns_processing_status(self, mock_poster_class, mock_config):
        """Returns processing status in response."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        # Process recording first
        client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE_STATUS_STATUS",
                "RecordingStatus": "completed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        # Check status
        response = client.get("/status/RE_STATUS_STATUS")
        data = response.json()

        assert data["status"] == "success"


# =============================================================================
# App Factory Tests
# =============================================================================

class TestCreateApp:
    """Tests for create_app factory function."""

    def test_creates_fastapi_app(self, mock_config):
        """Creates a FastAPI application."""
        from fastapi import FastAPI

        app = create_app(mock_config)

        assert isinstance(app, FastAPI)

    def test_app_has_correct_title(self, mock_config):
        """App has correct title."""
        app = create_app(mock_config)

        assert app.title == "vCon Twilio Adapter"

    def test_app_has_health_route(self, mock_config):
        """App has health check route."""
        app = create_app(mock_config)
        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200

    def test_app_has_webhook_route(self, mock_config):
        """App has webhook route."""
        app = create_app(mock_config)
        client = TestClient(app)

        response = client.post(
            "/webhook/recording",
            data={"RecordingSid": "RE123", "RecordingStatus": "in-progress"}
        )
        assert response.status_code == 200

    def test_app_has_status_route(self, mock_config):
        """App has status route."""
        app = create_app(mock_config)
        client = TestClient(app)

        # Use unique ID that won't exist in state file
        response = client.get("/status/RE_NON_EXISTENT_TEST_12345")
        # 404 is expected for non-existent recording
        assert response.status_code == 404


# =============================================================================
# Signature Validation Tests
# =============================================================================

class TestTwilioSignatureValidation:
    """Tests for Twilio signature validation."""

    def test_validation_disabled_accepts_all(self, test_client):
        """With validation disabled, all requests are accepted."""
        response = test_client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE_NO_SIG",
                "RecordingStatus": "in-progress",
            },
            # No X-Twilio-Signature header
        )

        assert response.status_code == 200

    def test_validation_enabled_rejects_invalid(self, minimal_env):
        """With validation enabled, invalid signature is rejected."""
        minimal_env.setenv("VALIDATE_TWILIO_SIGNATURE", "true")
        minimal_env.setenv("TWILIO_AUTH_TOKEN", "test_token_12345")
        minimal_env.setenv("DOWNLOAD_RECORDINGS", "false")

        config = Config()
        app = create_app(config)
        client = TestClient(app)

        response = client.post(
            "/webhook/recording",
            data={
                "RecordingSid": "RE_INVALID_SIG",
                "RecordingStatus": "completed",
            },
            headers={"X-Twilio-Signature": "invalid_signature"}
        )

        assert response.status_code == 403


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestWebhookErrorHandling:
    """Tests for error handling in webhook."""

    def test_returns_ok_even_on_build_failure(self, mock_config):
        """Returns OK even when vCon build fails."""
        with patch("twilio_adapter.webhook.VconBuilder") as mock_builder_class:
            mock_builder = MagicMock()
            mock_builder.build.return_value = None  # Simulate build failure
            mock_builder_class.return_value = mock_builder

            app = create_app(mock_config)
            client = TestClient(app)

            recording_sid = unique_recording_sid("RE_BUILD_FAIL")
            response = client.post(
                "/webhook/recording",
                data={
                    "RecordingSid": recording_sid,
                    "RecordingStatus": "completed",
                    "From": "+15551234567",
                    "To": "+15559876543",
                }
            )

            # Should still return OK to prevent Twilio retries
            assert response.status_code == 200
            assert response.text == "OK"

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_returns_ok_even_on_post_failure(self, mock_poster_class, mock_config):
        """Returns OK even when conserver post fails."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = False  # Simulate post failure
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        recording_sid = unique_recording_sid("RE_POST_FAIL")
        response = client.post(
            "/webhook/recording",
            data={
                "RecordingSid": recording_sid,
                "RecordingStatus": "completed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )

        # Should still return OK to prevent Twilio retries
        assert response.status_code == 200
        assert response.text == "OK"


# =============================================================================
# Integration-Style Tests
# =============================================================================

class TestWebhookIntegration:
    """Integration-style tests for the webhook."""

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_full_recording_flow(self, mock_poster_class, mock_config):
        """Test full recording processing flow."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        recording_sid = unique_recording_sid("RE_FLOW_TEST")

        # 1. Receive in-progress status (ignored)
        response = client.post(
            "/webhook/recording",
            data={
                "RecordingSid": recording_sid,
                "RecordingStatus": "in-progress",
            }
        )
        assert response.status_code == 200

        # 2. Receive completed status (processed)
        response = client.post(
            "/webhook/recording",
            data={
                "RecordingSid": recording_sid,
                "RecordingStatus": "completed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )
        assert response.status_code == 200

        # 3. Check status
        response = client.get(f"/status/{recording_sid}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

        # 4. Receive duplicate (skipped)
        mock_poster.post.reset_mock()
        response = client.post(
            "/webhook/recording",
            data={
                "RecordingSid": recording_sid,
                "RecordingStatus": "completed",
                "From": "+15551234567",
                "To": "+15559876543",
            }
        )
        assert response.status_code == 200
        mock_poster.post.assert_not_called()

    @patch("twilio_adapter.webhook.HttpPoster")
    def test_multiple_recordings(self, mock_poster_class, mock_config):
        """Test processing multiple different recordings."""
        mock_poster = MagicMock()
        mock_poster.post.return_value = True
        mock_poster_class.return_value = mock_poster

        app = create_app(mock_config)
        client = TestClient(app)

        # Generate unique recording SIDs
        recording_sids = [unique_recording_sid(f"RE_MULTI_{i}") for i in range(5)]

        # Process multiple recordings
        for i, rec_sid in enumerate(recording_sids):
            response = client.post(
                "/webhook/recording",
                data={
                    "RecordingSid": rec_sid,
                    "RecordingStatus": "completed",
                    "From": f"+1555000{i:04d}",
                    "To": "+15559876543",
                }
            )
            assert response.status_code == 200

        # Verify all were processed
        assert mock_poster.post.call_count == 5

        # Verify all have status
        for rec_sid in recording_sids:
            response = client.get(f"/status/{rec_sid}")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
