"""FastAPI webhook receiver for Telnyx recording events."""

import base64
import hashlib
import logging
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import PlainTextResponse

from .builder import TelnyxRecordingData, TelnyxVconBuilder
from .config import TelnyxConfig
from core.poster import HttpPoster
from core.tracker import StateTracker


logger = logging.getLogger(__name__)


def create_app(config: TelnyxConfig) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Application configuration

    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title="vCon Telnyx Adapter",
        description="Receives Telnyx recording events and creates vCons",
        version="0.1.0"
    )

    # Initialize components
    builder = TelnyxVconBuilder(
        download_recordings=config.download_recordings,
        recording_format=config.recording_format,
        api_key=config.telnyx_api_key,
    )
    poster = HttpPoster(
        config.conserver_url,
        config.get_headers(),
        config.ingress_lists
    )
    tracker = StateTracker(config.state_file)

    def validate_telnyx_signature(
        request_body: bytes,
        signature: Optional[str],
        timestamp: Optional[str],
    ) -> bool:
        """Validate Telnyx webhook signature.

        Telnyx uses a signature scheme based on timestamp and body.

        Args:
            request_body: Raw request body bytes
            signature: Telnyx-Signature-ed25519 header
            timestamp: Telnyx-Timestamp header

        Returns:
            True if valid or validation disabled
        """
        if not config.validate_webhook:
            return True

        if not config.telnyx_public_key:
            logger.warning("Webhook validation enabled but no public key configured")
            return True

        if not signature or not timestamp:
            return False

        try:
            # Telnyx signature validation
            # The signature is: ed25519(timestamp + "." + body)
            signed_payload = f"{timestamp}.".encode() + request_body

            # Decode the base64 signature
            signature_bytes = base64.b64decode(signature)

            # Verify using ed25519 (requires cryptography library)
            try:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                    Ed25519PublicKey
                )
                public_key_bytes = base64.b64decode(config.telnyx_public_key)
                public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
                public_key.verify(signature_bytes, signed_payload)
                return True
            except ImportError:
                logger.warning(
                    "cryptography library not installed, skipping signature validation"
                )
                return True
            except Exception:
                return False

        except Exception as e:
            logger.warning(f"Signature validation error: {e}")
            return False

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy", "service": "vcon-telnyx-adapter"}

    @app.post("/webhook/recording", response_class=PlainTextResponse)
    async def recording_event(
        request: Request,
        telnyx_signature_ed25519: Optional[str] = Header(default=None),
        telnyx_timestamp: Optional[str] = Header(default=None),
    ):
        """Handle Telnyx recording webhook event.

        This endpoint receives call.recording.saved events from
        Telnyx when call recordings are completed.
        """
        # Get raw body for signature validation
        body = await request.body()

        # Validate signature
        if not validate_telnyx_signature(body, telnyx_signature_ed25519, telnyx_timestamp):
            logger.warning("Invalid Telnyx webhook signature")
            raise HTTPException(status_code=403, detail="Invalid signature")

        # Parse JSON body
        try:
            event_data = await request.json()
        except Exception as e:
            logger.error(f"Failed to parse JSON body: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON")

        # Check event type
        data = event_data.get("data", event_data)
        event_type = data.get("event_type", "")

        # Only process recording saved events
        if event_type not in (
            "call.recording.saved",
            "call_recording.saved",
            "recording.saved"
        ):
            logger.debug(f"Ignoring Telnyx event type: {event_type}")
            return "OK"

        # Parse recording data
        recording_data = TelnyxRecordingData(event_data)
        recording_id = recording_data.recording_id

        if not recording_id:
            logger.warning("No recording ID in Telnyx event")
            return "OK"

        logger.info(
            f"Received Telnyx recording event: recording_id={recording_id}"
        )

        # Check if already processed
        if tracker.is_processed(recording_id):
            logger.info(f"Recording {recording_id} already processed, skipping")
            return "OK"

        # Build vCon
        vcon = builder.build(recording_data)
        if not vcon:
            logger.error(f"Failed to build vCon for recording {recording_id}")
            tracker.mark_processed(
                recording_id,
                "",
                status="build_failed",
                call_session_id=recording_data.call_session_id,
                from_number=recording_data.from_number,
                to_number=recording_data.to_number
            )
            return "OK"

        # Post to conserver
        success = poster.post(vcon)

        if success:
            tracker.mark_processed(
                recording_id,
                vcon.uuid,
                status="success",
                call_session_id=recording_data.call_session_id,
                from_number=recording_data.from_number,
                to_number=recording_data.to_number
            )
            logger.info(
                f"Successfully processed recording {recording_id} -> vCon {vcon.uuid}"
            )
        else:
            tracker.mark_processed(
                recording_id,
                vcon.uuid,
                status="post_failed",
                call_session_id=recording_data.call_session_id,
                from_number=recording_data.from_number,
                to_number=recording_data.to_number
            )
            logger.error(
                f"Failed to post vCon {vcon.uuid} for recording {recording_id}"
            )

        return "OK"

    @app.get("/status/{recording_id}")
    async def get_recording_status(recording_id: str):
        """Get processing status for a recording.

        Args:
            recording_id: Telnyx recording ID

        Returns:
            Processing status information
        """
        if not tracker.is_processed(recording_id):
            raise HTTPException(status_code=404, detail="Recording not found")

        return {
            "recording_id": recording_id,
            "vcon_uuid": tracker.get_vcon_uuid(recording_id),
            "status": tracker.get_processing_status(recording_id)
        }

    return app
