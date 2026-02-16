"""FastAPI webhook receiver for FreeSWITCH recording events."""

import hashlib
import hmac
import logging
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import PlainTextResponse

from .builder import FreeSwitchRecordingData, FreeSwitchVconBuilder
from .config import FreeSwitchConfig
from core.poster import HttpPoster
from core.tracker import StateTracker


logger = logging.getLogger(__name__)


def create_app(config: FreeSwitchConfig) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Application configuration

    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title="vCon FreeSWITCH Adapter",
        description="Receives FreeSWITCH recording events and creates vCons",
        version="0.1.0"
    )

    # Initialize components
    builder = FreeSwitchVconBuilder(
        download_recordings=config.download_recordings,
        recording_format=config.recording_format,
        recordings_path=config.recordings_path,
        recordings_url_base=config.recordings_url_base,
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
        return {"status": "healthy", "service": "vcon-freeswitch-adapter"}

    @app.post("/webhook/recording", response_class=PlainTextResponse)
    async def recording_event(
        request: Request,
        x_freeswitch_signature: Optional[str] = Header(default=None),
    ):
        """Handle FreeSWITCH recording event webhook.

        This endpoint receives events from FreeSWITCH when recordings
        are completed. Events can come from mod_http_cache, custom
        Lua scripts, or dialplan HTTP requests.
        """
        # Get raw body for signature validation
        body = await request.body()

        # Validate signature
        if not validate_signature(body, x_freeswitch_signature):
            logger.warning("Invalid FreeSWITCH webhook signature")
            raise HTTPException(status_code=403, detail="Invalid signature")

        # Parse JSON body
        try:
            event_data = await request.json()
        except Exception as e:
            logger.error(f"Failed to parse JSON body: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON")

        # Extract recording ID
        recording_id = event_data.get(
            "uuid",
            event_data.get("call_uuid", "")
        )

        if not recording_id:
            logger.warning("No recording ID in FreeSWITCH event")
            return "OK"

        logger.info(
            f"Received FreeSWITCH recording event: uuid={recording_id}"
        )

        # Check if already processed
        if tracker.is_processed(recording_id):
            logger.info(f"Recording {recording_id} already processed, skipping")
            return "OK"

        # Parse recording data
        recording_data = FreeSwitchRecordingData(event_data)

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
            recording_id: FreeSWITCH call UUID

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
