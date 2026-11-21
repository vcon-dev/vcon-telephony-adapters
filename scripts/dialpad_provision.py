#!/usr/bin/env python3
"""Create the Dialpad webhook and call event subscription for the dialpad adapter.

Two calls, both authenticated with ``Authorization: Bearer <api key>``:

1. ``POST /api/v2/webhooks`` with ``{hook_url, secret}``. Returns the webhook ``id``.
   ``hook_url`` is required; ``secret`` is optional, but without it Dialpad sends
   unsigned JSON.
2. ``POST /api/v2/subscriptions/call`` with ``{endpoint_id, call_states, enabled}``.

Verified against the Dialpad reference pages on 2026-10-20: both paths, the body
field names, the ``call_states`` enum, and the ``id`` in the webhook response.
Not confirmed: the subscription reference says "A webhook_id is required" but its
schema only lists ``endpoint_id`` (the id of the endpoint created in step 1). This
script sends ``endpoint_id`` on that reading; check against a live account.
The webhook response ``id`` may be an int or a string (the docs disagree); it is
passed through unchanged and coerced to int only when it is all digits.

The API key and secret are never printed.

Usage:
    DIALPAD_API_KEY=... DIALPAD_WEBHOOK_SECRET=... \\
        python scripts/dialpad_provision.py --hook-url https://host/webhook/call-event
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import requests

DEFAULT_API_BASE = "https://dialpad.com"
DEFAULT_STATES = "recording,call_transcription,voicemail_uploaded,recap_summary"


class DialpadProvisionError(RuntimeError):
    """A Dialpad API call failed. Never carries the API key or secret."""


def _post(base: str, path: str, api_key: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = requests.post(
        f"{base.rstrip('/')}{path}",
        json=body,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise DialpadProvisionError(
            f"POST {path} failed: HTTP {resp.status_code} {resp.text[:300]}"
        )
    return resp.json()


def webhook_body(hook_url: str, secret: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"hook_url": hook_url}
    if secret:
        body["secret"] = secret
    return body


def subscription_body(endpoint_id: Any, states: list[str]) -> dict[str, Any]:
    if isinstance(endpoint_id, str) and endpoint_id.isdigit():
        endpoint_id = int(endpoint_id)
    return {"endpoint_id": endpoint_id, "call_states": states, "enabled": True}


def provision(
    api_base: str, api_key: str, hook_url: str, secret: str | None, states: list[str]
) -> dict[str, Any]:
    webhook = _post(api_base, "/api/v2/webhooks", api_key, webhook_body(hook_url, secret))
    sub = _post(
        api_base,
        "/api/v2/subscriptions/call",
        api_key,
        subscription_body(webhook.get("id"), states),
    )
    return {"webhook_id": webhook.get("id"), "subscription_id": sub.get("id")}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--api-key", default=os.environ.get("DIALPAD_API_KEY"))
    p.add_argument("--api-base", default=os.environ.get("DIALPAD_API_BASE", DEFAULT_API_BASE))
    p.add_argument("--hook-url", default=os.environ.get("DIALPAD_HOOK_URL"))
    p.add_argument("--secret", default=os.environ.get("DIALPAD_WEBHOOK_SECRET"))
    p.add_argument("--states", default=DEFAULT_STATES, help="comma-separated call states")
    p.add_argument("--dry-run", action="store_true", help="print the requests, send nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    states = [s.strip() for s in args.states.split(",") if s.strip()]
    if not args.hook_url:
        print("error: --hook-url or DIALPAD_HOOK_URL is required", file=sys.stderr)
        return 2
    if not args.api_key and not args.dry_run:
        print("error: --api-key or DIALPAD_API_KEY is required", file=sys.stderr)
        return 2
    if not args.secret:
        print("warning: no secret set; Dialpad will send unsigned JSON", file=sys.stderr)

    if args.dry_run:
        plan = [
            {
                "POST": f"{args.api_base.rstrip('/')}/api/v2/webhooks",
                "body": {
                    "hook_url": args.hook_url,
                    "secret": "<redacted>" if args.secret else None,
                },
            },
            {
                "POST": f"{args.api_base.rstrip('/')}/api/v2/subscriptions/call",
                "body": subscription_body("<webhook id>", states),
            },
        ]
        print(json.dumps(plan, indent=2))
        return 0

    try:
        result = provision(args.api_base, args.api_key, args.hook_url, args.secret, states)
    except (DialpadProvisionError, requests.RequestException) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
