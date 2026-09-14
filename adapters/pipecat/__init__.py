"""Pipecat adapter — accumulates conversation state from a Pipecat pipeline and emits vCons.

Pipecat (https://github.com/pipecat-ai/pipecat) is a voice-AI agent framework. Unlike
webhook-based telephony adapters or pollers, Pipecat integration happens inside the
running pipeline: a `VconConversationObserver` is registered as a frame processor
that observes TranscriptionFrame, LLMResponseFrame, etc., accumulates state, and
emits a spec-compliant vCon at end-of-conversation.

See `README.md` in this directory for the integration pattern.
"""

from .builder import PipecatTurn, PipecatVconBuilder
from .observer import VconConversationObserver

__all__ = [
    "PipecatTurn",
    "PipecatVconBuilder",
    "VconConversationObserver",
]
