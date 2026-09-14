"""Pipecat FrameProcessor that accumulates conversation state into a vCon.

Designed to plug into a Pipecat pipeline. Listens for TranscriptionFrame,
LLMTextFrame / LLMFullResponseEndFrame, and EndFrame; builds a `PipecatConversationState`
incrementally; emits a vCon on EndFrame.

USAGE (rough sketch — verify against your Pipecat version):

    from pipecat.pipeline.pipeline import Pipeline
    from adapters.pipecat import VconConversationObserver

    observer = VconConversationObserver(
        conversation_id="call-123",
        on_vcon=lambda v: my_poster.post(v),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        observer,           # <-- intercepts user transcripts
        llm,
        observer,           # <-- same instance also intercepts LLM output
        tts,
        transport.output(),
    ])

This module deliberately imports `pipecat` lazily so the rest of the monorepo
doesn't need it. Install Pipecat separately to use this adapter:

    pip install pipecat-ai
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Callable

from vcon import Vcon

from .builder import PipecatConversationState, PipecatTurn, PipecatVconBuilder

logger = logging.getLogger(__name__)


class VconConversationObserver:
    """Pipecat FrameProcessor that builds a vCon as the conversation unfolds.

    NOTE: this class is intentionally NOT a subclass of Pipecat's `FrameProcessor`
    at import time — that would force every user of this monorepo to install
    pipecat-ai. The real subclass is created lazily via `as_frame_processor()`.
    """

    def __init__(
        self,
        *,
        conversation_id: str,
        on_vcon: Callable[[Vcon], None],
        user_party: dict | None = None,
        agent_party: dict | None = None,
        builder: PipecatVconBuilder | None = None,
    ):
        self.state = PipecatConversationState(
            conversation_id=conversation_id,
            started_at=datetime.now(UTC),
            user_party=user_party,
            agent_party=agent_party,
        )
        self.on_vcon = on_vcon
        self.builder = builder or PipecatVconBuilder()
        self._pending_assistant_text = ""

    # ------------------------------------------------------------------
    # Public state-mutating hooks (call from a FrameProcessor wrapper)
    # ------------------------------------------------------------------
    def record_user_transcript(self, text: str, *, when: datetime | None = None) -> None:
        self.state.turns.append(PipecatTurn(
            role="user", text=text, start_time=when or datetime.now(UTC)
        ))

    def record_assistant_text_chunk(self, chunk: str) -> None:
        """LLMs stream tokens — buffer until end-of-response."""
        self._pending_assistant_text += chunk

    def finalize_assistant_turn(self, *, when: datetime | None = None) -> None:
        if not self._pending_assistant_text:
            return
        self.state.turns.append(PipecatTurn(
            role="assistant",
            text=self._pending_assistant_text,
            start_time=when or datetime.now(UTC),
        ))
        self._pending_assistant_text = ""

    def attach_audio(self, audio_bytes: bytes, *, mediatype: str = "audio/wav") -> None:
        self.state.audio_bytes = audio_bytes
        self.state.audio_mediatype = mediatype

    def end(self) -> Vcon:
        """Mark conversation end, build vCon, invoke callback, return it."""
        if self._pending_assistant_text:
            self.finalize_assistant_turn()
        self.state.ended_at = datetime.now(UTC)
        vcon = self.builder.build(self.state)
        try:
            self.on_vcon(vcon)
        except Exception:
            logger.exception("on_vcon callback raised")
        return vcon

    # ------------------------------------------------------------------
    # Pipecat integration (lazy — only imports pipecat when called)
    # ------------------------------------------------------------------
    def as_frame_processor(self) -> Any:
        """Return a real `pipecat.processors.frame_processor.FrameProcessor`
        subclass that delegates frame events to this observer.

        Requires `pipecat-ai` installed.
        """
        try:
            from pipecat.frames.frames import (  # type: ignore
                EndFrame,
                Frame,
                LLMFullResponseEndFrame,
                LLMTextFrame,
                TranscriptionFrame,
            )
            from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "pipecat-ai is not installed. `pip install pipecat-ai` to use this adapter."
            ) from e

        observer = self

        class _VconFrameProcessor(FrameProcessor):
            async def process_frame(self, frame: Frame, direction: FrameDirection):
                await super().process_frame(frame, direction)
                if isinstance(frame, TranscriptionFrame):
                    observer.record_user_transcript(frame.text)
                elif isinstance(frame, LLMTextFrame):
                    observer.record_assistant_text_chunk(frame.text)
                elif isinstance(frame, LLMFullResponseEndFrame):
                    observer.finalize_assistant_turn()
                elif isinstance(frame, EndFrame):
                    observer.end()
                await self.push_frame(frame, direction)

        return _VconFrameProcessor()
