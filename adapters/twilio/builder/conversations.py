"""vCon builder for Twilio Conversations API webhooks."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

from .._common import strip_empty_dict_placeholders

logger = logging.getLogger(__name__)

MESSAGE_EVENTS = frozenset(
    {
        "onMessageAdded",
        "onMessageUpdated",
        "onDeliveryUpdated",
    }
)


class TwilioConversationsBuilder:
    """Build vCons from Twilio Conversations API event webhooks."""

    ADAPTER_SOURCE = "twilio_adapter"

    def should_process(self, payload: dict[str, Any]) -> bool:
        event_type = payload.get("EventType") or payload.get("event_type", "")
        return event_type in MESSAGE_EVENTS

    def build(self, payload: dict[str, Any]) -> Vcon | None:
        event_type = payload.get("EventType") or payload.get("event_type", "")
        if event_type not in MESSAGE_EVENTS:
            return None

        try:
            vcon = Vcon.build_new()
            conversation_sid = (
                payload.get("ConversationSid")
                or payload.get("conversation_sid")
                or ""
            )
            message_sid = payload.get("MessageSid") or payload.get("message_sid", "")
            author = payload.get("Author") or payload.get("author", "unknown")
            body = payload.get("Body") or payload.get("body", "")

            participants = payload.get("Participants") or []
            party_index: dict[str, int] = {}

            if participants:
                for p in participants:
                    identity = p.get("identity") or p.get("messaging_binding", {}).get(
                        "address", "participant"
                    )
                    idx = len(vcon.vcon_dict.get("parties", []))
                    vcon.add_party(Party(name=identity))
                    party_index[p.get("sid", identity)] = idx
            else:
                vcon.add_party(Party(name=author))
                vcon.add_party(Party(name="conversation", role="platform"))
                party_index["author"] = 0

            originator = party_index.get(payload.get("ParticipantSid", ""), 0)
            start = datetime.now(timezone.utc)

            channel = (payload.get("ChannelType") or payload.get("channel_type") or "chat").lower()
            application = f"twilio_conversations_{channel}"

            vcon.add_dialog(
                Dialog(
                    type="text",
                    start=start,
                    parties=[0, 1] if len(vcon.vcon_dict.get("parties", [])) >= 2 else [0],
                    originator=originator,
                    body=body,
                    encoding="none",
                    mediatype="text/plain",
                    message_id=message_sid or None,
                    application=application,
                    session_id=conversation_sid or None,
                )
            )
            strip_empty_dict_placeholders(vcon.vcon_dict["dialog"][-1])

            media = payload.get("Media") or payload.get("media")
            if media:
                vcon.add_attachment(
                    purpose="conversation_media",
                    body=json.dumps(media) if isinstance(media, (list, dict)) else str(media),
                    encoding="json",
                    party=0,
                    dialog=0,
                )

            vcon.add_tag("source", self.ADAPTER_SOURCE)
            vcon.add_tag("communication_mode", channel.upper())
            if conversation_sid:
                vcon.add_tag("conversation_sid", conversation_sid)
            if message_sid:
                vcon.add_tag("message_sid", message_sid)
            vcon.add_tag("event_type", event_type)

            return vcon

        except Exception as e:
            logger.error(f"Error building conversations vCon: {e}")
            return None
