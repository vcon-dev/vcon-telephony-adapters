"""FastAPI webhook receiver for Telnyx recording events."""

import asyncio
import base64
import logging

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

from core.poster import HttpPoster
from core.tracker import StateTracker

from .builder import TelnyxRecordingData, TelnyxVconBuilder
from .call_session import CallSessionStore
from .config import TelnyxConfig
from .provision import TelnyxProvisioner, handle_call_event

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
        version="0.1.0",
    )

    # Initialize components
    sessions = CallSessionStore()
    lookup = (
        TelnyxProvisioner(config.telnyx_api_key, base_url=config.telnyx_api_url)
        if config.telnyx_api_key
        else None
    )

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

    builder = TelnyxVconBuilder(
        download_recordings=config.download_recordings,
        recording_format=config.recording_format,
        api_key=config.telnyx_api_key,
        publisher=publisher,
        lawful_basis=lawful_basis,
    )
    poster = HttpPoster(config.conserver_url, config.get_headers(), config.ingress_lists)
    tracker = StateTracker(config.state_file)

    def validate_telnyx_signature(
        request_body: bytes,
        signature: str | None,
        timestamp: str | None,
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
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

                public_key_bytes = base64.b64decode(config.telnyx_public_key)
                public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
                public_key.verify(signature_bytes, signed_payload)
                return True
            except ImportError:
                logger.warning("cryptography library not installed, skipping signature validation")
                return True
            except Exception:
                return False

        except Exception as e:
            logger.warning(f"Signature validation error: {e}")
            return False

    # Smart trunk: on every answered call, either fork to our SRS (auto_siprec)
    # or stream to the vcon-realtime bridge (auto_stream), using the customer's
    # own Telnyx key. Only mounted when explicitly enabled and keyed.
    capture_on = config.auto_siprec or config.auto_stream
    provisioner = (
        TelnyxProvisioner(config.telnyx_api_key, base_url=config.telnyx_api_url)
        if capture_on and config.telnyx_api_key
        else None
    )
    if capture_on and not provisioner:
        logger.error("TELNYX_AUTO_SIPREC/STREAM is on but TELNYX_API_KEY is unset; capturing nothing")
    if config.auto_stream and not config.stream_url:
        logger.error("TELNYX_AUTO_STREAM is on but TELNYX_STREAM_URL is unset; not streaming any calls")
    if config.auto_siprec and config.auto_stream:
        logger.warning(
            "Both TELNYX_AUTO_SIPREC and TELNYX_AUTO_STREAM are on; Telnyx allows one "
            "stream-or-fork per call, so the fork wins and the stream is skipped"
        )

    @app.post("/webhook/call", response_class=PlainTextResponse)
    async def call_event(
        request: Request,
        telnyx_signature_ed25519: str | None = Header(default=None),
        telnyx_timestamp: str | None = Header(default=None),
    ):
        """Handle a Telnyx call lifecycle event and start the SIPREC fork.

        Always returns 200. A recording we failed to start is recoverable; a
        non-2xx here just makes Telnyx retry an event whose call has moved on.
        """
        body = await request.body()
        if not validate_telnyx_signature(body, telnyx_signature_ed25519, telnyx_timestamp):
            logger.warning("Invalid Telnyx webhook signature on call event")
            raise HTTPException(status_code=403, detail="Invalid signature")

        try:
            event_data = await request.json()
        except Exception:
            logger.error("Failed to parse JSON body on call event")
            return "OK"

        # Always accumulate. `call.recording.saved` carries no party identity,
        # so these events are the only in-band source of who was on the call.
        sessions.record(event_data)

        if not provisioner:
            return "OK"

        # Run the capture start off the event loop: it is a blocking Telnyx API
        # call, and the 2026-09-15 spike showed a sync call inside an async handler
        # freezes the loop for its whole duration.
        call_control_id = await asyncio.to_thread(
            handle_call_event,
            event_data,
            provisioner,
            config.siprec_connector_name if config.auto_siprec else None,
            config.transcribe_realtime,
            config.stream_url if config.auto_stream else None,
            config.stream_track,
        )
        if call_control_id:
            logger.info("Started capture on call %s", call_control_id)

        return "OK"

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy", "service": "vcon-telnyx-adapter"}

    @app.post("/webhook/recording", response_class=PlainTextResponse)
    async def recording_event(
        request: Request,
        telnyx_signature_ed25519: str | None = Header(default=None),
        telnyx_timestamp: str | None = Header(default=None),
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
            raise HTTPException(status_code=400, detail="Invalid JSON") from None

        return await _process_recording_event(event_data)

    async def _process_recording_event(event_data: dict) -> str:
        """Shared by the Call Control JSON webhook and the TeXML form callback."""
        # Check event type
        data = event_data.get("data", event_data)
        event_type = data.get("event_type", "")

        # Only process recording saved events
        if event_type not in ("call.recording.saved", "call_recording.saved", "recording.saved"):
            logger.debug(f"Ignoring Telnyx event type: {event_type}")
            return "OK"

        # Resolve who was on the call. The recording event does not say.
        payload = data.get("payload", {})
        session = sessions.get(payload.get("call_session_id", ""))

        recording_meta = None
        if (session is None or not session.has_parties) and lookup:
            # No lifecycle events for this call (a restart, a dropped webhook,
            # a replay). The recordings API is authoritative and stateless.
            try:
                recording_meta = lookup.get_recording(payload.get("recording_id", ""))
                logger.info("Resolved party identity from the recordings API")
            except Exception as exc:
                logger.warning("Recording lookup failed: %s", exc)

        recording_data = TelnyxRecordingData(
            event_data, session=session, recording_meta=recording_meta
        )
        recording_id = recording_data.recording_id

        if not recording_id:
            logger.warning("No recording ID in Telnyx event")
            return "OK"

        logger.info(f"Received Telnyx recording event: recording_id={recording_id}")

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
                call_session_id=recording_data.call_session_id,
                from_number=recording_data.from_number,
                to_number=recording_data.to_number,
            )
            logger.info(f"Successfully processed recording {recording_id} -> vCon {vcon.uuid}")
            sessions.discard(payload.get("call_session_id", ""))
        else:
            tracker.mark_processed(
                recording_id,
                vcon.uuid,
                status="post_failed",
                call_session_id=recording_data.call_session_id,
                from_number=recording_data.from_number,
                to_number=recording_data.to_number,
            )
            logger.error(f"Failed to post vCon {vcon.uuid} for recording {recording_id}")

        return "OK"

    @app.post("/webhook/texml-recording", response_class=PlainTextResponse)
    async def texml_recording_event(request: Request):
        """TeXML `recordingStatusCallback` (form-encoded, path B of the smart trunk).

        Reshaped into the Call Control `call.recording.saved` payload so one
        builder serves both. Measured fields, 2026-09-13: RecordingSid,
        CallSessionId, RecordingUrl (pre-signed S3 mp3, 600 s), RecordingChannels
        "1"|"2", RecordingStartTime/EndTime, From, To, Direction, ConnectionId.
        Unlike Call Control webhooks these carry From and To, so no lookup is needed.

        ponytail: no signature check here. TeXML callbacks are not ed25519 signed the
        way Call Control webhooks are; gate this path with a per-tenant token in the
        URL when the multi-tenant hook lands (CON-846).
        """
        form = await request.form()
        f = {k: str(v) for k, v in form.items()}
        if f.get("RecordingStatus", "completed") != "completed" or not f.get("RecordingUrl"):
            return "OK"
        url = f["RecordingUrl"]
        ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
        direction = {"inbound": "incoming", "outbound": "outgoing"}.get(f.get("Direction", "").lower(), "")
        payload = {
            "recording_id": f.get("RecordingSid", ""),
            "call_session_id": f.get("CallSessionId", ""),
            "call_control_id": f.get("CallSid", ""),
            "connection_id": f.get("ConnectionId", ""),
            "recording_urls": {ext if ext in ("mp3", "wav") else "mp3": url},
            "channels": "dual" if f.get("RecordingChannels") == "2" else "single",
            "recording_started_at": f.get("RecordingStartTime"),
            "recording_ended_at": f.get("RecordingEndTime"),
            "from": f.get("From", ""),
            "to": f.get("To", ""),
            "direction": direction,
            "recording_source": f.get("RecordingSource", "texml"),
        }
        event_data = {"data": {"event_type": "call.recording.saved", "record_type": "event",
                               "occurred_at": f.get("RecordingEndTime"), "payload": payload}}
        return await _process_recording_event(event_data)

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
            "status": tracker.get_processing_status(recording_id),
        }

    return app
