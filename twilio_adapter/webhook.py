"""FastAPI webhook receiver for Twilio recording status callbacks."""

import logging
from typing import Optional
from fastapi import FastAPI, Form, Request, HTTPException, Depends
from fastapi.responses import PlainTextResponse
from twilio.request_validator import RequestValidator

from .builder import TwilioRecordingData, VconBuilder
from .poster import HttpPoster
from .tracker import StateTracker
from .config import Config


logger = logging.getLogger(__name__)


def create_app(config: Config) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Application configuration

    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title="vCon Twilio Adapter",
        description="Receives Twilio recording webhooks and creates vCons",
        version="0.1.0"
    )

    # Initialize components
    builder = VconBuilder(
        download_recordings=config.download_recordings,
        recording_format=config.recording_format,
        twilio_auth=config.get_twilio_auth()
    )
    poster = HttpPoster(
        config.conserver_url,
        config.get_headers(),
        config.ingress_lists
    )
    tracker = StateTracker(config.state_file)

    # Twilio signature validator
    validator = None
    if config.validate_twilio_signature and config.twilio_auth_token:
        validator = RequestValidator(config.twilio_auth_token)

    async def validate_twilio_request(request: Request) -> bool:
        """Validate that request came from Twilio.

        Args:
            request: FastAPI request object

        Returns:
            True if request is valid

        Raises:
            HTTPException: If validation fails
        """
        if not config.validate_twilio_signature:
            return True

        if not validator:
            logger.warning("Signature validation enabled but validator not configured")
            return True

        # Get the URL for validation
        url = config.webhook_url or str(request.url)

        # Get the signature header
        signature = request.headers.get("X-Twilio-Signature", "")

        # Get form data for validation
        form_data = await request.form()
        params = {key: value for key, value in form_data.items()}

        # Validate the request
        if not validator.validate(url, params, signature):
            logger.warning(f"Invalid Twilio signature for request to {url}")
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

        return True

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy", "service": "vcon-telephony-adapters-twilio"}

    @app.post("/webhook/recording", response_class=PlainTextResponse)
    async def recording_status_callback(
        request: Request,
        RecordingSid: str = Form(...),
        AccountSid: str = Form(default=""),
        CallSid: str = Form(default=""),
        RecordingUrl: str = Form(default=""),
        RecordingStatus: str = Form(default=""),
        RecordingDuration: Optional[str] = Form(default=None),
        RecordingChannels: str = Form(default="1"),
        RecordingSource: str = Form(default=""),
        RecordingStartTime: Optional[str] = Form(default=None),
        From: str = Form(default="", alias="From"),
        To: str = Form(default="", alias="To"),
        Caller: str = Form(default=""),
        Called: str = Form(default=""),
        Direction: str = Form(default=""),
        CallStatus: str = Form(default=""),
        ApiVersion: str = Form(default=""),
        ForwardedFrom: Optional[str] = Form(default=None),
        CallerCity: Optional[str] = Form(default=None),
        CallerState: Optional[str] = Form(default=None),
        CallerZip: Optional[str] = Form(default=None),
        CallerCountry: Optional[str] = Form(default=None),
        CalledCity: Optional[str] = Form(default=None),
        CalledState: Optional[str] = Form(default=None),
        CalledZip: Optional[str] = Form(default=None),
        CalledCountry: Optional[str] = Form(default=None),
    ):
        """Handle Twilio recording status callback.

        This endpoint receives webhooks from Twilio when recordings are ready.
        It creates a vCon from the recording data and posts it to the conserver.
        """
        # Validate Twilio signature
        await validate_twilio_request(request)

        logger.info(
            f"Received recording callback: RecordingSid={RecordingSid}, "
            f"Status={RecordingStatus}, CallSid={CallSid}"
        )

        # Only process completed recordings
        if RecordingStatus != "completed":
            logger.info(
                f"Ignoring recording {RecordingSid} with status: {RecordingStatus}"
            )
            return "OK"

        # Check if already processed
        if tracker.is_processed(RecordingSid):
            logger.info(f"Recording {RecordingSid} already processed, skipping")
            return "OK"

        # Build webhook data dictionary
        webhook_data = {
            "RecordingSid": RecordingSid,
            "AccountSid": AccountSid,
            "CallSid": CallSid,
            "RecordingUrl": RecordingUrl,
            "RecordingStatus": RecordingStatus,
            "RecordingDuration": RecordingDuration,
            "RecordingChannels": RecordingChannels,
            "RecordingSource": RecordingSource,
            "RecordingStartTime": RecordingStartTime,
            "From": From,
            "To": To,
            "Caller": Caller or From,
            "Called": Called or To,
            "Direction": Direction,
            "CallStatus": CallStatus,
            "ApiVersion": ApiVersion,
            "ForwardedFrom": ForwardedFrom,
            "CallerCity": CallerCity,
            "CallerState": CallerState,
            "CallerZip": CallerZip,
            "CallerCountry": CallerCountry,
            "CalledCity": CalledCity,
            "CalledState": CalledState,
            "CalledZip": CalledZip,
            "CalledCountry": CalledCountry,
        }

        # Parse recording data
        recording_data = TwilioRecordingData(webhook_data)

        # Build vCon
        vcon = builder.build(recording_data)
        if not vcon:
            logger.error(f"Failed to build vCon for recording {RecordingSid}")
            tracker.mark_processed(
                RecordingSid,
                "",
                status="build_failed",
                call_sid=CallSid,
                from_number=From,
                to_number=To
            )
            return "OK"

        # Post to conserver
        success = poster.post(vcon)

        if success:
            tracker.mark_processed(
                RecordingSid,
                vcon.uuid,
                status="success",
                call_sid=CallSid,
                from_number=From,
                to_number=To
            )
            logger.info(
                f"Successfully processed recording {RecordingSid} -> vCon {vcon.uuid}"
            )
        else:
            tracker.mark_processed(
                RecordingSid,
                vcon.uuid,
                status="post_failed",
                call_sid=CallSid,
                from_number=From,
                to_number=To
            )
            logger.error(
                f"Failed to post vCon {vcon.uuid} for recording {RecordingSid}"
            )

        # Always return 200 OK to Twilio to prevent retries
        return "OK"

    @app.get("/status/{recording_sid}")
    async def get_recording_status(recording_sid: str):
        """Get processing status for a recording.

        Args:
            recording_sid: Twilio RecordingSid

        Returns:
            Processing status information
        """
        if not tracker.is_processed(recording_sid):
            raise HTTPException(status_code=404, detail="Recording not found")

        return {
            "recording_sid": recording_sid,
            "vcon_uuid": tracker.get_vcon_uuid(recording_sid),
            "status": tracker.get_processing_status(recording_sid)
        }

    return app
