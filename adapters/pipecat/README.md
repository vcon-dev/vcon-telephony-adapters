# Pipecat adapter

[Pipecat](https://github.com/pipecat-ai/pipecat) is an open-source framework for building voice-AI agents in Python. Unlike webhook telephony adapters (Twilio, Telnyx, …), Pipecat integration happens **inside the running pipeline**: a frame processor accumulates conversation state and emits a vCon at end-of-call.

This adapter does NOT poll an API or receive webhooks. It plugs into your Pipecat pipeline.

## Install

```bash
pip install vcon-telephony-adapters pipecat-ai
```

`pipecat-ai` is intentionally **not** a hard dependency of `vcon-telephony-adapters` so the rest of the monorepo doesn't need to install it. Install it separately when using this adapter.

## How it works

Your Pipecat pipeline produces frames (TranscriptionFrame, LLMTextFrame, EndFrame, …). The `VconConversationObserver`:

1. Records user transcripts as text dialogs (role=user, originator=0)
2. Buffers LLM response chunks across `LLMTextFrame`s and finalizes the turn on `LLMFullResponseEndFrame`
3. (Optionally) attaches recorded audio bytes via `observer.attach_audio(bytes, mediatype="audio/wav")`
4. On `EndFrame`, builds a spec-compliant vCon (syntax 0.4.0) and invokes your `on_vcon` callback (typically a webhook poster)

## Wiring it in

```python
from pipecat.pipeline.pipeline import Pipeline
from adapters.pipecat import VconConversationObserver

def post_to_conserver(vcon):
    # your delivery logic — e.g. core.poster.HttpPoster
    print(vcon.to_json())

observer = VconConversationObserver(
    conversation_id="call-abc-123",
    on_vcon=post_to_conserver,
    user_party={"name": "Customer", "tel": "+15551234567", "role": "user"},
    agent_party={"name": "Agent", "role": "agent"},
)
frame_processor = observer.as_frame_processor()

pipeline = Pipeline([
    transport.input(),
    stt,
    frame_processor,   # observe user transcripts
    llm,
    frame_processor,   # observe LLM responses
    tts,
    transport.output(),
])
```

## Standalone usage (no Pipecat installed)

If you want to construct vCons from your own accumulated state without going through Pipecat at all:

```python
from datetime import datetime, timezone
from adapters.pipecat import (
    PipecatConversationState, PipecatTurn, PipecatVconBuilder,
)

state = PipecatConversationState(
    conversation_id="call-123",
    started_at=datetime.now(timezone.utc),
    user_party={"name": "Customer", "tel": "+15551234567"},
    agent_party={"name": "Agent", "role": "agent"},
    turns=[
        PipecatTurn(role="user", text="Hi", start_time=datetime.now(timezone.utc)),
        PipecatTurn(role="assistant", text="Hello! How can I help?", start_time=datetime.now(timezone.utc)),
    ],
)
vcon = PipecatVconBuilder().build(state)
```

## Spec compliance

The builder routes everything through vcon-lib helpers (`add_party`, `add_dialog`, `add_attachment`, `add_analysis`, `add_tag`) per the project's "always use lib helpers" rule. Dialogs use spec-correct `mediatype` (not `mimetype`); inline audio uses `encoding="base64url"` with a `content_hash`. The shared spec-compliance smoke tests in `tests/test_spec_compliance.py` cover the cross-adapter contract; `tests/adapters/pipecat/` covers the Pipecat-specific shape.
