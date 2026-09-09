"""Bring-your-own-key provisioning for a Telnyx smart trunk.

The customer keeps their own Telnyx account and hands us an API key. We use it to
point their account's SIPREC client at an SRS we operate, and to start a SIPREC
session on each call. Their carriage, billing, and numbers stay theirs; we never
sit in the media path.

Three pieces:

* ``TelnyxProvisioner`` — the API calls, idempotent where it matters.
* ``handle_call_event`` — decides whether an inbound webhook should start a fork.
  A pure function so it is testable without a network or a web server.
* a CLI (``python -m adapters.telnyx.provision``) for provision / status / deprovision.

The API key belongs to someone else. It is never logged, never written to disk by
this module, and is redacted from any error this module raises.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.telnyx.com/v2"

# Telnyx fires these as a call progresses. We fork as early as we can still get
# whole-call audio: `call.answered`, because a fork started at `call.initiated`
# would cover ringing but the leg may never be answered at all.
FORK_ON_EVENTS = ("call.answered",)

# SIP transport for the fork. `tls` implies SRTP on the media, which our SRS
# cannot decrypt yet — see CON-800. Until that lands, `udp` behind a source-IP
# allowlist is the only working option, and it is a pilot-grade compromise.
DEFAULT_SIP_TRANSPORT = "udp"


class TelnyxError(RuntimeError):
    """A Telnyx API call failed. Never carries the API key."""


@dataclass
class Connector:
    """A provisioned SIPREC connector in the customer's Telnyx account."""

    name: str
    host: str
    port: int

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Connector:
        return cls(name=data["name"], host=data["host"], port=int(data["port"]))


class TelnyxProvisioner:
    """Talks to one customer's Telnyx account.

    Args:
        api_key: the customer's Telnyx API key. Theirs, not ours.
        base_url: override for testing against a mock.
        session: inject a ``requests.Session`` (or any object with ``.request``)
            to test without a network.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        session: Any | None = None,
    ):
        if not api_key:
            raise ValueError("A Telnyx API key is required")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()

    def _redact(self, text: str) -> str:
        """Strip the customer's key out of anything we are about to raise or log."""
        return text.replace(self._api_key, "***") if self._api_key in text else text

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            response = self._session.request(
                method,
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            raise TelnyxError(self._redact(f"{method} {path} failed: {exc}")) from None

        if response.status_code == 401:
            raise TelnyxError(
                "Telnyx rejected the API key (401). Check the key and its permissions."
            )
        if response.status_code == 404:
            return {}
        if response.status_code >= 400:
            raise TelnyxError(
                self._redact(f"{method} {path} -> {response.status_code}: {response.text[:400]}")
            )

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise TelnyxError(f"{method} {path} returned non-JSON body") from None

    # -- key validation ----------------------------------------------------

    def check_key(self) -> bool:
        """Confirm the key works, by listing connectors.

        Doubles as the permission check: a key that cannot list SIPREC connectors
        cannot provision one either, so there is no point discovering that later.
        """
        self._request("GET", "/siprec_connectors")
        return True

    # -- connectors --------------------------------------------------------

    def list_connectors(self) -> list[Connector]:
        body = self._request("GET", "/siprec_connectors")
        return [Connector.from_api(item) for item in body.get("data", [])]

    def find_connector(self, name: str) -> Connector | None:
        return next((c for c in self.list_connectors() if c.name == name), None)

    def ensure_connector(self, name: str, host: str, port: int) -> Connector:
        """Create the connector, or correct it if it already exists.

        Idempotent on purpose: provisioning gets re-run, whether or not anyone
        intended it to be.
        """
        existing = self.find_connector(name)
        payload = {"name": name, "host": host, "port": int(port)}

        if existing is None:
            body = self._request("POST", "/siprec_connectors", payload)
            logger.info("Created SIPREC connector %s -> %s:%s", name, host, port)
            return Connector.from_api(body.get("data", payload))

        if (existing.host, existing.port) == (host, int(port)):
            logger.info("SIPREC connector %s already points at %s:%s", name, host, port)
            return existing

        body = self._request("PATCH", f"/siprec_connectors/{name}", payload)
        logger.info(
            "Repointed SIPREC connector %s from %s:%s to %s:%s",
            name,
            existing.host,
            existing.port,
            host,
            port,
        )
        return Connector.from_api(body.get("data", payload))

    def delete_connector(self, name: str) -> bool:
        """Remove the connector. Returns False if it was already gone."""
        if self.find_connector(name) is None:
            return False
        self._request("DELETE", f"/siprec_connectors/{name}")
        logger.info("Deleted SIPREC connector %s", name)
        return True

    # -- account inspection (read-only) ------------------------------------

    def list_connections(self) -> list[dict[str, Any]]:
        """Connections in the customer's account, with their webhook URLs."""
        return self._request("GET", "/connections").get("data", [])

    def list_phone_numbers(self) -> list[dict[str, Any]]:
        return self._request("GET", "/phone_numbers").get("data", [])

    def inspect(self) -> dict[str, Any]:
        """Report what is in the account before we change anything.

        Read-only and deliberately so. Under BYOK we act inside someone else's
        production telephony account, and a connection carries exactly one
        webhook URL: setting ours over a URL their own application depends on
        breaks it silently. Every webhook strategy in CON-845 requires reading
        first, so this is the one part that is safe to build before that
        decision is made.
        """
        connections = []
        for c in self.list_connections():
            connections.append(
                {
                    "id": c.get("id"),
                    "name": c.get("connection_name") or c.get("name"),
                    "type": c.get("record_type"),
                    "webhook_event_url": c.get("webhook_event_url"),
                    "webhook_failover_url": c.get("webhook_event_failover_url"),
                    "active": c.get("active"),
                }
            )
        numbers = [
            {
                "number": n.get("phone_number"),
                "connection_id": n.get("connection_id"),
                "status": n.get("status"),
            }
            for n in self.list_phone_numbers()
        ]
        occupied = [c for c in connections if c["webhook_event_url"]]
        return {
            "connections": connections,
            "phone_numbers": numbers,
            "webhook_urls_already_set": len(occupied),
        }

    def get_recording(self, recording_id: str) -> dict[str, Any] | None:
        """Fetch a recording's metadata.

        The stateless source of party identity. `call.recording.saved` omits
        `from`, `to` and `duration_millis`, but the recordings resource carries
        all three, so a vCon can name its parties even when the lifecycle
        events were missed entirely (a restart, a dropped webhook, a replay).
        """
        body = self._request("GET", f"/recordings/{recording_id}")
        return body.get("data") or None

    # -- per-call actions --------------------------------------------------

    def start_siprec(
        self,
        call_control_id: str,
        connector_name: str,
        track: str = "both_tracks",
        sip_transport: str = DEFAULT_SIP_TRANSPORT,
    ) -> dict:
        """Fork this call's media to the connector's SRS."""
        return self._request(
            "POST",
            f"/calls/{call_control_id}/actions/siprec_start",
            {
                "connector_name": connector_name,
                "siprec_track": track,
                "sip_transport": sip_transport,
            },
        )

    def stop_siprec(self, call_control_id: str) -> dict:
        return self._request("POST", f"/calls/{call_control_id}/actions/siprec_stop", {})

    def start_transcription(
        self,
        call_control_id: str,
        engine: str = "B",
        tracks: str = "both",
    ) -> dict:
        """Have Telnyx transcribe the call and webhook us the results.

        This is realtime without us running a streaming recogniser. It is billed
        per minute by Telnyx, on the customer's own account — which under BYOK is
        the point: their spend, their bill, visible to them.
        """
        return self._request(
            "POST",
            f"/calls/{call_control_id}/actions/transcription_start",
            {"transcription_engine": engine, "transcription_tracks": tracks},
        )

    # -- top level ---------------------------------------------------------

    def provision(self, name: str, host: str, port: int) -> Connector:
        self.check_key()
        return self.ensure_connector(name, host, port)

    def deprovision(self, name: str) -> bool:
        return self.delete_connector(name)


def handle_call_event(
    event: dict[str, Any],
    provisioner: TelnyxProvisioner,
    connector_name: str,
    transcribe: bool = False,
) -> str | None:
    """Start a fork if this webhook is a call we should be recording.

    Returns the ``call_control_id`` we acted on, or None if the event was not one
    we fork on. Pure enough to test without a server.

    A failure here must not break the call: the customer's call completes whether
    or not we manage to record it. That asymmetry is the whole reason we stay out
    of the media path, so this swallows errors rather than propagating them.
    """
    payload = event.get("data", event).get("payload", {})
    event_type = event.get("data", event).get("event_type", "")

    if event_type not in FORK_ON_EVENTS:
        return None

    call_control_id = payload.get("call_control_id")
    if not call_control_id:
        logger.warning("Telnyx %s event carried no call_control_id", event_type)
        return None

    try:
        provisioner.start_siprec(call_control_id, connector_name)
        if transcribe:
            provisioner.start_transcription(call_control_id)
    except TelnyxError as exc:
        # Losing a recording is recoverable. Breaking a call is not.
        logger.error("Could not start SIPREC on call %s: %s", call_control_id, exc)
        return None

    return call_control_id


# -- CLI -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m adapters.telnyx.provision",
        description="Point a customer's own Telnyx account at our SIPREC SRS.",
    )
    parser.add_argument(
        "action", choices=("check", "inspect", "provision", "status", "deprovision")
    )
    parser.add_argument("--name", default=os.getenv("TELNYX_CONNECTOR_NAME", "vconic-smart-trunk"))
    parser.add_argument("--host", default=os.getenv("SRS_HOST"), help="Our SRS hostname or IP")
    parser.add_argument("--port", type=int, default=int(os.getenv("SRS_PORT", "5060")))
    parser.add_argument("--base-url", default=os.getenv("TELNYX_API_URL", DEFAULT_BASE_URL))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    api_key = os.getenv("TELNYX_API_KEY")
    if not api_key:
        print("Set TELNYX_API_KEY to the customer's Telnyx API key.", file=sys.stderr)
        return 2

    p = TelnyxProvisioner(api_key, base_url=args.base_url)

    try:
        if args.action == "check":
            p.check_key()
            print("Key works and can read SIPREC connectors.")

        elif args.action == "provision":
            if not args.host:
                print("--host (or SRS_HOST) is required to provision.", file=sys.stderr)
                return 2
            c = p.provision(args.name, args.host, args.port)
            print(f"Provisioned: {c.name} -> {c.host}:{c.port}")
            if args.port != 5061:
                print(
                    "Note: media will be plain RTP. Restrict this port to Telnyx "
                    "source IPs until SRTP support lands.",
                    file=sys.stderr,
                )

        elif args.action == "inspect":
            report = p.inspect()
            print(f"{len(report['connections'])} connection(s):")
            for c in report["connections"]:
                url = c["webhook_event_url"] or "(none)"
                print(f"  {c['name'] or c['id']}  [{c['type']}]  webhook: {url}")
                if c["webhook_failover_url"]:
                    print(f"      failover: {c['webhook_failover_url']}")
            print(f"{len(report['phone_numbers'])} phone number(s):")
            for n in report["phone_numbers"][:20]:
                print(f"  {n['number']}  connection={n['connection_id']}  {n['status']}")
            if report["webhook_urls_already_set"]:
                print(
                    f"\nWARNING: {report['webhook_urls_already_set']} connection(s) already have a "
                    "webhook URL set. Overwriting one breaks whatever is consuming it. "
                    "Decide the strategy (CON-845) before provisioning.",
                    file=sys.stderr,
                )

        elif args.action == "status":
            connectors = p.list_connectors()
            if not connectors:
                print("No SIPREC connectors in this account.")
            for c in connectors:
                marker = " <- ours" if c.name == args.name else ""
                print(f"{c.name}: {c.host}:{c.port}{marker}")

        elif args.action == "deprovision":
            print("Removed." if p.deprovision(args.name) else "Nothing to remove.")

    except TelnyxError as exc:
        print(f"Telnyx error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
