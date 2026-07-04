"""Pydantic models for Twilio webhook payloads (optional validation layer)."""

from pydantic import BaseModel, Field


class TwilioMessagingWebhook(BaseModel):
    message_sid: str = Field(alias="MessageSid")
    account_sid: str = Field(default="", alias="AccountSid")
    from_number: str = Field(default="", alias="From")
    to_number: str = Field(default="", alias="To")
    body: str = Field(default="", alias="Body")
    num_media: int = Field(default=0, alias="NumMedia")
    sms_status: str = Field(default="", alias="SmsStatus")
    direction: str = Field(default="inbound", alias="Direction")

    model_config = {"populate_by_name": True}


class TwilioVoiceStatusWebhook(BaseModel):
    call_sid: str = Field(alias="CallSid")
    call_status: str = Field(alias="CallStatus")
    from_number: str = Field(default="", alias="From")
    to_number: str = Field(default="", alias="To")
    direction: str = Field(default="", alias="Direction")

    model_config = {"populate_by_name": True}


class TwilioConversationsWebhook(BaseModel):
    event_type: str = Field(alias="EventType")
    conversation_sid: str = Field(default="", alias="ConversationSid")
    message_sid: str = Field(default="", alias="MessageSid")
    author: str = Field(default="", alias="Author")
    body: str = Field(default="", alias="Body")

    model_config = {"populate_by_name": True}
