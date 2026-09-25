"""Configuration for the Pipecat adapter.

Pipecat (https://github.com/pipecat-ai/pipecat) is a voice-AI pipeline
framework, not a service that calls a webhook: the integration runs inside
the user's own pipeline process (see `observer.py`). There is no inbound
request to authenticate, so this config carries no webhook-validation
settings — only the pieces every adapter needs to post a finished vCon to
the conserver with a lawful basis and (optionally) re-hosted audio.

`python main.py pipecat` starts a minimal FastAPI app exposing only
`/health`, so the adapter registers and containerizes like the others; the
real integration point is importing `VconConversationObserver` into a
Pipecat pipeline (see the package docstring in `__init__.py`).
"""

import os

from core.base_config import BaseConfig


class PipecatConfig(BaseConfig):
    """Pipecat-specific configuration."""

    def __init__(self, env_file: str | None = None):
        super().__init__(env_file)

        self.state_file = os.getenv("STATE_FILE", ".pipecat_adapter_state.json")

        self.customer_party_name = os.getenv("PIPECAT_USER_NAME")
        self.agent_party_name = os.getenv("PIPECAT_AGENT_NAME", "Agent")
