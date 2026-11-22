"""Helpers that keep phone numbers and signed URLs out of log lines.

A pre-signed recording URL is a bearer credential until it expires, and a phone
number is personal data. Neither belongs in a log. Use these on anything that
reaches a logger: numbers through `mask_number`, URLs through `redact_url`, and
exception text (a `requests` error often quotes the URL) through `redact_text`.
"""

from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://[^\s'\"<>)]+", re.IGNORECASE)


def mask_number(number: object) -> str:
    """Reduce a phone number to its last four digits, e.g. `***0101`.

    Returns `-` for an empty value and `***` when fewer than four digits remain.
    """
    if not number:
        return "-"
    digits = re.sub(r"\D", "", str(number))
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


def redact_url(url: object) -> str:
    """Drop the query string and fragment, and any userinfo, from a URL."""
    if not url:
        return ""
    text = str(url).split("#", 1)[0].split("?", 1)[0]
    return re.sub(r"^(https?://)[^/@]*@", r"\1", text, flags=re.IGNORECASE)


def redact_text(text: object) -> str:
    """Apply `redact_url` to every URL found in free text, such as an exception message."""
    return _URL_RE.sub(lambda m: redact_url(m.group(0)), str(text))
