"""FastAPI webhook receiver for Twilio (voice, messaging, fax, video, conversations)."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from twilio.request_validator import RequestValidator

from core.poster import HttpPoster
from core.tracker import StateTracker

from .builder import (
    TwilioConversationsBuilder,
    TwilioFaxBuilder,
    TwilioMessagingBuilder,
    TwilioRecordingData,
    TwilioVconBuilder,
    TwilioVideoBuilder,
    TwilioVoiceStatusBuilder,
)
from .config import TwilioConfig
from .session import SessionAggregator

logger = logging.getLogger(__name__)


def create_app(config: TwilioConfig) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="vCon Twilio Adapter",
        description="Receives Twilio webhooks and creates vCons for all communication modes",
        version="0.2.0",
    )

    auth = config.get_twilio_auth()
    recording_builder = TwilioVconBuilder(
        download_recordings=config.download_recordings,
        recording_format=config.recording_format,
        twilio_auth=auth,
    )
    voice_status_builder = TwilioVoiceStatusBuilder()
    messaging_builder = TwilioMessagingBuilder(
        twilio_auth=auth,
        download_media=config.download_messaging_media,
    )
    fax_builder = TwilioFaxBuilder(twilio_auth=auth, download_fax=config.download_fax)
    video_builder = TwilioVideoBuilder(
        twilio_auth=auth,
        download_video=config.download_video,
        account_sid=config.twilio_account_sid,
    )
    conversations_builder = TwilioConversationsBuilder()

    poster = HttpPoster(config.conserver_url, config.get_headers(), config.ingress_lists)
    tracker = StateTracker(config.state_file)
    sessions = SessionAggregator(tracker, config.messaging_session_window_hours)

    validator = None
    if config.validate_twilio_signature and config.twilio_auth_token:
        validator = RequestValidator(config.twilio_auth_token)

    async def validate_twilio_request(request: Request, form_data: dict | None = None) -> dict:
        if form_data is None:
            form_data = dict((await request.form()).items())

        if not config.validate_twilio_signature:
            return form_data

        if not validator:
            logger.warning("Signature validation enabled but validator not configured")
            return form_data

        url = config.webhook_url or str(request.url)
        signature = request.headers.get("X-Twilio-Signature", "")

        if not validator.validate(url, form_data, signature):
            logger.warning(f"Invalid Twilio signature for request to {url}")
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

        return form_data

    def _post_vcon(
        resource_id: str,
        vcon,
        *,
        call_sid: str = "",
        from_number: str = "",
        to_number: str = "",
    ) -> PlainTextResponse:
        success = poster.post(vcon)
        status = "success" if success else "post_failed"
        tracker.mark_processed(
            resource_id,
            vcon.uuid,
            status=status,
            call_sid=call_sid,
            from_number=from_number,
            to_number=to_number,
        )
        if success:
            logger.info(f"Processed {resource_id} -> vCon {vcon.uuid}")
        else:
            logger.error(f"Failed to post vCon {vcon.uuid} for {resource_id}")
        return PlainTextResponse("OK")

    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "service": "vcon-telephony-adapters-twilio",
            "modes": {
                "voice_recording": config.enable_voice_recording,
                "voice_status": config.enable_voice_status,
                "messaging": config.enable_messaging,
                "fax": config.enable_fax,
                "video": config.enable_video,
                "conversations": config.enable_conversations,
            },
        }

    @app.get("/webhook/demo")
    @app.post("/webhook/demo")
    async def demo_twiml(request: Request):
        recording_callback = (
            config.webhook_url or str(request.base_url).rstrip("/") + "/webhook/recording"
        )
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say>Please leave a message after the beep.</Say>
  <Record
    recordingStatusCallback="{recording_callback}"
    recordingStatusCallbackMethod="POST"
    recordingStatusCallbackEvent="completed"
    maxLength="120"
    playBeep="true"
  />
  <Say>Goodbye.</Say>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

    @app.post("/webhook/recording", response_class=PlainTextResponse)
    async def recording_status_callback(request: Request):
        if not config.enable_voice_recording:
            return "OK"

        form = await validate_twilio_request(request)
        recording_sid = form.get("RecordingSid", "")
        recording_status = form.get("RecordingStatus", "")

        logger.info(
            f"Recording callback: RecordingSid={recording_sid}, Status={recording_status}"
        )

        if recording_status != "completed":
            return "OK"

        if tracker.is_processed(recording_sid):
            logger.info(f"Recording {recording_sid} already processed")
            return "OK"

        recording_data = TwilioRecordingData(form)
        vcon = recording_builder.build(recording_data)
        if not vcon:
            tracker.mark_processed(recording_sid, "", status="build_failed")
            return "OK"

        return _post_vcon(
            recording_sid,
            vcon,
            call_sid=form.get("CallSid", ""),
            from_number=form.get("From", ""),
            to_number=form.get("To", ""),
        )

    @app.post("/webhook/voice/status", response_class=PlainTextResponse)
    async def voice_status_callback(request: Request):
        if not config.enable_voice_status:
            return "OK"

        form = await validate_twilio_request(request)
        call_sid = form.get("CallSid", "")
        if not call_sid or tracker.is_processed(f"call:{call_sid}"):
            return "OK"

        if not voice_status_builder.should_process(form):
            return "OK"

        vcon = voice_status_builder.build(form)
        if not vcon:
            return "OK"

        return _post_vcon(
            f"call:{call_sid}",
            vcon,
            call_sid=call_sid,
            from_number=form.get("From", ""),
            to_number=form.get("To", ""),
        )

    @app.post("/webhook/messaging", response_class=PlainTextResponse)
    async def messaging_callback(request: Request):
        if not config.enable_messaging:
            return "OK"

        form = await validate_twilio_request(request)
        message_sid = form.get("MessageSid", "")
        if not message_sid or tracker.is_processed(message_sid):
            return "OK"

        if not messaging_builder.should_process_inbound(form) and not messaging_builder.should_process_status(
            form
        ):
            return "OK"

        vcon = messaging_builder.build(form)
        if not vcon:
            return "OK"

        from ._common import detect_messaging_channel

        channel = detect_messaging_channel(form)
        sessions.touch_session(
            sessions.session_key(
                from_addr=form.get("From", ""),
                to_addr=form.get("To", ""),
                channel=channel,
            ),
            vcon.uuid,
        )

        return _post_vcon(
            message_sid,
            vcon,
            from_number=form.get("From", ""),
            to_number=form.get("To", ""),
        )

    @app.post("/webhook/fax", response_class=PlainTextResponse)
    async def fax_callback(request: Request):
        if not config.enable_fax:
            return "OK"

        form = await validate_twilio_request(request)
        fax_sid = form.get("FaxSid", "")
        if not fax_sid or tracker.is_processed(fax_sid):
            return "OK"

        if not fax_builder.should_process(form):
            return "OK"

        vcon = fax_builder.build(form)
        if not vcon:
            return "OK"

        return _post_vcon(
            fax_sid,
            vcon,
            from_number=form.get("From", ""),
            to_number=form.get("To", ""),
        )

    @app.post("/webhook/video", response_class=PlainTextResponse)
    async def video_callback(request: Request):
        if not config.enable_video:
            return "OK"

        form = await validate_twilio_request(request)
        resource_id = form.get("CompositionSid") or form.get("RecordingSid") or form.get("RoomSid", "")
        if not resource_id or tracker.is_processed(resource_id):
            return "OK"

        if not video_builder.should_process(form):
            return "OK"

        vcon = video_builder.build(form)
        if not vcon:
            return "OK"

        return _post_vcon(resource_id, vcon)

    @app.post("/webhook/conversations", response_class=PlainTextResponse)
    async def conversations_callback(request: Request):
        if not config.enable_conversations:
            return "OK"

        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload: dict[str, Any] = await request.json()
        else:
            form = await validate_twilio_request(request)
            payload = dict(form)

        event_type = payload.get("EventType") or payload.get("event_type", "")
        message_sid = payload.get("MessageSid") or payload.get("message_sid", "")
        conversation_sid = payload.get("ConversationSid") or payload.get("conversation_sid", "")
        resource_id = message_sid or f"{conversation_sid}:{event_type}"

        if not resource_id or tracker.is_processed(resource_id):
            return "OK"

        if not conversations_builder.should_process(payload):
            return "OK"

        vcon = conversations_builder.build(payload)
        if not vcon:
            return "OK"

        if conversation_sid:
            sessions.touch_session(sessions.conversation_key(conversation_sid), vcon.uuid)

        return _post_vcon(resource_id, vcon)

    @app.get("/status/{resource_id}")
    async def get_resource_status(resource_id: str):
        if not tracker.is_processed(resource_id):
            raise HTTPException(status_code=404, detail="Resource not found")

        return {
            "resource_id": resource_id,
            "recording_sid": resource_id if resource_id.startswith("RE") else None,
            "vcon_uuid": tracker.get_vcon_uuid(resource_id),
            "status": tracker.get_processing_status(resource_id),
        }

    return app
