"""ElevenLabs API response models (pydantic)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConversationParticipant(BaseModel):
    id: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    role: str = "participant"


class AudioRecording(BaseModel):
    url: str | None = None
    duration_ms: int
    sample_rate: int = 16000
    channels: int = 1
    format: str = "wav"
    size_bytes: int | None = None


class Transcript(BaseModel):
    text: str
    language: str = "en"
    confidence: float | None = None


class Conversation(BaseModel):
    """An ElevenLabs agent conversation."""

    model_config = ConfigDict(use_enum_values=True)

    id: str
    agent_id: str
    start_time: datetime
    end_time: datetime | None = None
    duration_ms: int | None = None
    status: str = "completed"
    participants: list[ConversationParticipant]
    audio: AudioRecording | None = None
    transcript: Transcript | None = None
    metadata: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
