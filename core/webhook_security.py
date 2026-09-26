"""Shared webhook-timestamp helpers used by every adapter that signs a timestamp.

A provider that signs `{timestamp}|{body}` (or similar) is protecting the request
against tampering, not against replay: a captured, validly-signed request stays
valid forever unless the receiver also checks that the timestamp is recent. This
module holds that check so every adapter enforces it the same way, after its own
signature verification succeeds.

Timestamp formats for the two adapters that currently use this:

- Telnyx: `Telnyx-Timestamp` header, Unix seconds as a decimal string (matches the
  existing `{timestamp}|{raw body}` signed-payload construction already in
  adapters/telnyx/webhook.py and its tests). WebFetch of
  https://developers.telnyx.com/docs/development/api-fundamentals/webhooks/receiving-webhooks
  on 2026-09-26 confirms the page instructs implementers to "reject signatures
  outside the application's replay window" but the fetched page text does not
  spell out whether the header is seconds or milliseconds; Unix seconds is kept
  as the existing, already-tested assumption in this codebase.
- ElevenLabs: the `t=` field of the `elevenlabs-signature` header
  (`t=<timestamp>,v0=<hex_hmac>`), assumed Unix seconds as a decimal string.
  WebFetch of https://elevenlabs.io/docs/conversational-ai/guides/webhooks on
  2026-09-26 returned a 404, so this is unverified against current public docs;
  it follows the Stripe-style convention the `t=`/`v0=` header shape strongly
  resembles, matching the assumption already documented in
  adapters/elevenlabs/webhook.py. If wrong, this fails closed (rejects), it
  does not silently accept.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def parse_tolerance_seconds(raw: str, *, source: str) -> int:
    """Parse and validate a tolerance-seconds config value.

    Args:
        raw: The raw string value (typically from an environment variable).
        source: Name of the setting, used in the error message.

    Returns:
        The parsed positive integer.

    Raises:
        ValueError: If `raw` does not parse to a positive integer.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{source} must be a positive integer, got: {raw!r}") from None
    if value <= 0:
        raise ValueError(f"{source} must be a positive integer, got: {raw!r}")
    return value


def is_timestamp_fresh(timestamp: str | int | float | None, tolerance_seconds: int) -> bool:
    """Whether a signed Unix-seconds timestamp is within tolerance of now.

    Checks both directions: a timestamp too far in the past (a replayed old
    request) and one too far in the future (clock skew abuse or a probe) are
    both rejected. A missing or non-numeric timestamp is rejected, not treated
    as "no timestamp to check".

    Args:
        timestamp: The timestamp value as sent by the provider (Unix seconds).
        tolerance_seconds: Maximum allowed absolute difference, in seconds,
            between the timestamp and the current time.

    Returns:
        True if the timestamp is present, numeric, and within the window.
    """
    if timestamp is None:
        return False
    try:
        timestamp_value = float(timestamp)
    except (TypeError, ValueError):
        return False

    now = time.time()
    return abs(now - timestamp_value) <= tolerance_seconds
