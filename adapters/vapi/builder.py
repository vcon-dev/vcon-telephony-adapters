"""VAPI vCon builder.

VAPI (https://vapi.ai) emits an "end-of-call-report" webhook when a voice-AI
call completes. The payload contains:
- `startedAt` / `endedAt` ISO timestamps
- `artifact.messages[]` with role ("user", "bot", "system") and message text
- `recordingUrl` for the audio
- `transcript` (plain-text concatenated)
- `analysis.summary`, `analysis.successEvaluation`
- `cost`, `durationSeconds`, `endedReason`

This builder converts that payload into a spec-compliant vCon
(IETF draft-ietf-vcon-vcon-core-02, syntax 0.4.0) via the vcon-lib helpers
(>=0.9.4). Routes everything through the lib.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

logger = logging.getLogger(__name__)


class VapiVconBuilder:
    ADAPTER_SOURCE = "vapi"

    def __init__(
        self,
        *,
        customer_party_name: str = "Customer",
        agent_party_name: str = "AI Agent",
    ):
        self.customer_party_name = customer_party_name
        self.agent_party_name = agent_party_name

    # ------------------------------------------------------------------
    def build(self, vapi_message: dict[str, Any]) -> Vcon | None:
        """Build a vCon from a VAPI 'end-of-call-report' message payload."""
        try:
            if vapi_message.get("type") != "end-of-call-report":
                logger.debug("ignoring non-end-of-call VAPI message")
                return None

            vcon = Vcon.build_new()

            if vapi_message.get("startedAt"):
                vcon.vcon_dict["created_at"] = vapi_message["startedAt"]
            if vapi_message.get("endedAt"):
                vcon.vcon_dict["updated_at"] = vapi_message["endedAt"]

            # Parties: customer (index 0) and AI agent (index 1).
            vcon.add_party(Party(name=self.customer_party_name, role="customer"))
            vcon.add_party(Party(name=self.agent_party_name, role="agent"))

            # Each VAPI message becomes a text dialog. Role-mapping:
            #   "user" → originator=0 (customer)
            #   "bot"  → originator=1 (agent)
            #   anything else (system) → skipped
            artifact = vapi_message.get("artifact") or {}
            for msg in artifact.get("messages", []) or []:
                role = msg.get("role")
                if role not in ("user", "bot"):
                    continue
                start_iso = _epoch_ms_to_iso(msg.get("time"))
                vcon.add_dialog(Dialog(
                    type="text",
                    start=start_iso,
                    parties=[0, 1],
                    originator=0 if role == "user" else 1,
                    body=msg.get("message", ""),
                    encoding="none",
                    mediatype="text/plain",
                ))
                _strip_empty_placeholders(vcon.vcon_dict["dialog"][-1])

            # Recording URL as external-media-style attachment.
            # We don't have audio bytes from the webhook payload, so we emit url
            # alone with a warning that the dialog is not spec-compliant for
            # external media (which requires url + content_hash).
            if vapi_message.get("recordingUrl"):
                logger.warning(
                    "VAPI recordingUrl emitted without content_hash; "
                    "fetch + hash the audio for spec-compliant external media."
                )
                vcon.add_attachment(
                    purpose="recording_url",
                    body=vapi_message["recordingUrl"],
                    encoding="none",
                    party=0,
                    dialog=0,
                )

            # Plain-text transcript goes in analysis[] (NOT attachments) per the rule
            # "default to analysis[] for transcripts". Vendor REQUIRED on analysis.
            if vapi_message.get("transcript"):
                vcon.add_analysis(
                    type="transcript",
                    dialog=0,
                    vendor="vapi",
                    body=vapi_message["transcript"],
                    encoding="none",
                )

            # Call summary + success evaluation as separate analysis entries.
            analysis_block = vapi_message.get("analysis") or {}
            if analysis_block.get("summary"):
                vcon.add_analysis(
                    type="summary",
                    dialog=0,
                    vendor="vapi",
                    body=analysis_block["summary"],
                    encoding="none",
                )
            if "successEvaluation" in analysis_block:
                vcon.add_analysis(
                    type="success_evaluation",
                    dialog=0,
                    vendor="vapi",
                    body=json.dumps({"value": analysis_block["successEvaluation"]}),
                    encoding="json",
                )

            # Cost + duration + endedReason as a call_record attachment.
            call_record = {
                k: v
                for k, v in {
                    "cost": vapi_message.get("cost"),
                    "durationSeconds": vapi_message.get("durationSeconds"),
                    "endedReason": vapi_message.get("endedReason"),
                    "callId": vapi_message.get("call", {}).get("id")
                    if isinstance(vapi_message.get("call"), dict)
                    else None,
                }.items()
                if v is not None
            }
            if call_record:
                vcon.add_attachment(
                    purpose="call_record",
                    body=json.dumps(call_record),
                    encoding="json",
                    party=0,
                    dialog=0,
                )

            # Tags.
            vcon.add_tag("source", self.ADAPTER_SOURCE)
            if vapi_message.get("call", {}).get("id") if isinstance(vapi_message.get("call"), dict) else None:
                vcon.add_tag("call_id", vapi_message["call"]["id"])

            return vcon

        except Exception as e:
            logger.error(f"error building VAPI vCon: {e}")
            return None


def _epoch_ms_to_iso(epoch_ms: Any) -> str | None:
    if epoch_ms is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch_ms) / 1000.0).isoformat()
    except (ValueError, TypeError):
        return None


def _strip_empty_placeholders(d: dict, keys: tuple = ("meta", "metadata")) -> None:
    for k in keys:
        if k in d and d[k] == {}:
            del d[k]
