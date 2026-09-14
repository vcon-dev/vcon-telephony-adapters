"""VAPI adapter configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class VapiConfig:
    webhook_url: str
    webhook_auth_header_name: str = "x-conserver-api-token"
    webhook_auth_header_value: str | None = None
    customer_party_name: str = "Customer"
    agent_party_name: str = "AI Agent"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "VapiConfig":
        return cls(
            webhook_url=os.environ.get("WEBHOOK_URL", ""),
            webhook_auth_header_name=os.environ.get(
                "WEBHOOK_AUTH_HEADER_NAME", "x-conserver-api-token"
            ),
            webhook_auth_header_value=os.environ.get("WEBHOOK_AUTH_HEADER_VALUE"),
            customer_party_name=os.environ.get("VAPI_CUSTOMER_NAME", "Customer"),
            agent_party_name=os.environ.get("VAPI_AGENT_NAME", "AI Agent"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
