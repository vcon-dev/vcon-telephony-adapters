"""FastAPI webhook receiver for Asterisk recording events."""

import hashlib
import hmac
import logging
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import PlainTextResponse

from .builder import AsteriskRecordingData, AsteriskVconBuilder
from .config import AsteriskConfig
from core.poster import HttpPoster
from core.tracker import StateTracker


logger = logging.getLogger(__name__)


def create_app(config: AsteriskConfig) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Application configuration

    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title="vCon Asterisk Adapter",
        description="Receives Asterisk recording events and creates vCons",
        version="0.1.0"
    )

    # Initialize components
    builder = AsteriskVconBuilder(
        download_recordings=config.download_recordings,
        recording_format=config.recording_format,
        recordings_path=config.recordings_path,
        ari_url=config.asterisk_ari_url,
        ari_auth=config.get_ari_auth(),
    )
    poster = HttpPoster(
        config.conserver_url,
        config.get_headers(),
        config.ingress_lists
    )
    tracker = StateTracker(config.state_file)

    def validate_signature(
        request_body: bytes,
        signature: Optional[str]
    ) -> bool:
        """Validate webhook signature.

        Args:
            request_body: Raw request body bytes
            signature: Signature from header

        Returns:
            True if valid or validation disabled
        """
        if not config.validate_webhook:
            return True

        if not config.webhook_secret:
            logger.warning("Webhook validation enabled but no secret configured")
            return True

        if not signature:
            return False

        # Calculate expected signature
        expected = hmac.new(
            config.webhook_secret.encode(),
            request_body,
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(signature, expected)

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy", "service": "vcon-asterisk-adapter"}

    @app.post("/webhook/recording", response_class=PlainTextResponse)
    async def recording_event(
        request: Request,
        x_asterisk_signature: Optional[str] = Header(default=None),
    ):
        """Handle Asterisk recording event webhook.

        This endpoint receives ARI or AMI events when recordings
        are completed. Can be triggered via Stasis application
        or custom AGI scripts.
        """
        # Get raw body for signature validation
        body = await request.body()

        # Validate signature
        if not validate_signature(body, x_asterisk_signature):
            logger.warning("Invalid Asterisk webhook signature")
            raise HTTPException(status_code=403, detail="Invalid signature")

        # Parse JSON body
        try:
            event_data = await request.json()
        except Exception as e:
            logger.error(f"Failed to parse JSON body: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON")

        # Check event type (ARI sends various events)
        event_type = event_data.get("type", event_data.get("event"))

        # Only process recording finished events
        if event_type and event_type not in (
            "RecordingFinished",
            "recording_finished",
            "StasisEnd"
        ):
            logger.debug(f"Ignoring Asterisk event type: {event_type}")
            return "OK"

        # Extract recording ID
        recording_id = event_data.get(
            "recording_name",
            event_data.get("name", event_data.get("Uniqueid", ""))
        )

        if not recording_id:
            logger.warning("No recording ID in Asterisk event")
            return "OK"

        logger.info(
            f"Received Asterisk recording event: name={recording_id}, type={event_type}"
        )

        # Check if already processed
        if tracker.is_processed(recording_id):
            logger.info(f"Recording {recording_id} already processed, skipping")
            return "OK"

        # Parse recording data
        recording_data = AsteriskRecordingData(event_data)

        # Build vCon
        vcon = builder.build(recording_data)
        if not vcon:
            logger.error(f"Failed to build vCon for recording {recording_id}")
            tracker.mark_processed(
                recording_id,
                "",
                status="build_failed",
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
            recording_id: Asterisk recording name

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
