"""Pipecat adapter: accumulates conversation state from a Pipecat pipeline and
emits vCons.

Pipecat (https://github.com/pipecat-ai/pipecat) is a voice-AI agent
framework. Unlike webhook-based telephony adapters or pollers, the
integration happens inside the running pipeline: a `VconConversationObserver`
is registered as a frame processor that observes TranscriptionFrame,
LLMResponseFrame, etc., accumulates state, and emits a spec-compliant vCon at
end-of-conversation. See `observer.py` for the integration pattern.
"""

from .builder import PipecatConversationState, PipecatTurn, PipecatVconBuilder
from .config import PipecatConfig
from .observer import VconConversationObserver
from .webhook import create_app

__all__ = [
    "PipecatConfig",
    "PipecatConversationState",
    "PipecatTurn",
    "PipecatVconBuilder",
    "VconConversationObserver",
    "create_app",
]
