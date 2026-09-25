"""FastAPI webhook receiver for Bandwidth recording events."""

import logging
import secrets

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core.poster import HttpPoster
from core.tracker import StateTracker

from .builder import BandwidthRecordingData, BandwidthVconBuilder
from .config import BandwidthConfig

logger = logging.getLogger(__name__)


def create_app(config: BandwidthConfig) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Application configuration

    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title="vCon Bandwidth Adapter",
        description="Receives Bandwidth recording events and creates vCons",
        version="0.1.0",
    )

    # Initialize components
    publisher = config.build_publisher()
    if publisher is None:
        logger.warning(
            "MEDIA_BACKEND=embed: audio will be inlined as base64, making each "
            "vCon roughly 1.3x the size of the recording. Set MEDIA_BACKEND=s3 "
            "for anything beyond a lab."
        )
    lawful_basis = config.build_lawful_basis()
    if not lawful_basis.enabled:
        logger.warning(
            "LAWFUL_BASIS is unset, so vCons will carry no record of why this "
            "deployment may hold the recording. Fine for a lab; not for real "
            "conversations."
        )

    builder = BandwidthVconBuilder(
        download_recordings=config.download_recordings,
        recording_format=config.recording_format,
        api_auth=config.get_api_auth(),
        publisher=publisher,
        lawful_basis=lawful_basis,
    )
    poster = HttpPoster(config.conserver_url, config.get_headers(), config.ingress_lists)
    tracker = StateTracker(config.state_file)

    # HTTP Basic Auth for webhook validation
    security = HTTPBasic(auto_error=False)

    def validate_basic_auth(credentials: HTTPBasicCredentials | None) -> bool:
        """Validate HTTP Basic authentication.

        Args:
            credentials: HTTP Basic credentials from request

        Returns:
            True if valid or validation disabled
        """
        if not config.validate_webhook:
            return True

        if not config.webhook_username or not config.webhook_password:
            # Only reachable with ALLOW_UNSIGNED_WEBHOOKS=true: BandwidthConfig
            # refuses to start otherwise when validation is on with no credentials.
            logger.warning("Webhook validation enabled but no credentials configured")
            return True

        if not credentials:
            return False

        # Use secrets.compare_digest to prevent timing attacks
        username_correct = secrets.compare_digest(
            credentials.username.encode("utf-8"), config.webhook_username.encode("utf-8")
        )
        password_correct = secrets.compare_digest(
            credentials.password.encode("utf-8"), config.webhook_password.encode("utf-8")
        )

        return username_correct and password_correct

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy", "service": "vcon-bandwidth-adapter"}

    @app.post("/webhook/recording", response_class=PlainTextResponse)
    async def recording_event(
        request: Request,
        credentials: HTTPBasicCredentials | None = Depends(security),
    ):
        """Handle Bandwidth recording webhook event.

        This endpoint receives recordingComplete events from
        Bandwidth when call recordings are finished.
        """
        # Validate authentication
        if not validate_basic_auth(credentials):
            logger.warning("Invalid Bandwidth webhook authentication")
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

        # Parse JSON body
        try:
            event_data = await request.json()
        except Exception as e:
            logger.error(f"Failed to parse JSON body: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON") from None

        # Check event type
        event_type = event_data.get("eventType", "")

        # Only process recording complete events
        if event_type not in ("recordingComplete", "recording", "transcriptionAvailable"):
            logger.debug(f"Ignoring Bandwidth event type: {event_type}")
            return "OK"

        # Extract recording ID
        recording_id = event_data.get("recordingId", "")

        if not recording_id:
            logger.warning("No recording ID in Bandwidth event")
            return "OK"

        logger.info(
            f"Received Bandwidth recording event: recordingId={recording_id}, " f"type={event_type}"
        )

        # Check if already processed
        if tracker.is_processed(recording_id):
            logger.info(f"Recording {recording_id} already processed, skipping")
            return "OK"

        # Parse recording data
        recording_data = BandwidthRecordingData(event_data)

        # Build vCon
        vcon = builder.build(recording_data)
        if not vcon:
            logger.error(f"Failed to build vCon for recording {recording_id}")
            tracker.mark_processed(
                recording_id,
                "",
                status="build_failed",
                call_id=recording_data.call_id,
                from_number=recording_data.from_number,
                to_number=recording_data.to_number,
            )
            return "OK"

        # Post to conserver
        success = poster.post(vcon)

        if success:
            tracker.mark_processed(
                recording_id,
                vcon.uuid,
                status="success",
                call_id=recording_data.call_id,
                from_number=recording_data.from_number,
                to_number=recording_data.to_number,
            )
            logger.info(f"Successfully processed recording {recording_id} -> vCon {vcon.uuid}")
        else:
            tracker.mark_processed(
                recording_id,
                vcon.uuid,
                status="post_failed",
                call_id=recording_data.call_id,
                from_number=recording_data.from_number,
                to_number=recording_data.to_number,
            )
            logger.error(f"Failed to post vCon {vcon.uuid} for recording {recording_id}")

        return "OK"

    @app.get("/status/{recording_id}")
    async def get_recording_status(recording_id: str):
        """Get processing status for a recording.

        Args:
            recording_id: Bandwidth recording ID

        Returns:
            Processing status information
        """
        if not tracker.is_processed(recording_id):
            raise HTTPException(status_code=404, detail="Recording not found")

        return {
            "recording_id": recording_id,
            "vcon_uuid": tracker.get_vcon_uuid(recording_id),
            "status": tracker.get_processing_status(recording_id),
        }

    return app
