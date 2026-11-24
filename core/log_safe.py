"""Helpers that keep phone numbers and signed URLs out of log lines.

A pre-signed recording URL is a bearer credential until it expires, and a phone
number is personal data. Neither belongs in a log. Use these on anything that
reaches a logger: numbers through `mask_number`, URLs through `redact_url`, and
exception text (a `requests` error often quotes the URL) through `redact_text`.
"""

from __future__ import annotations

import copy
import logging
import re

# Characters allowed in a URL token, with balanced `( )` pairs tolerated so `/a(1).wav?x=1` is
# one token while a lone `)` that closes a parenthesized URL still ends it. Every alternative
# starts on a different character, so matching is linear.
_TOKEN = r"""(?:[^\s'"<>()]|\([^\s'"<>()]*\))"""
# scheme://authority/rest, where the authority may hold a bracketed IPv6 host: [2001:db8::1]:8443
_URL_RE = re.compile(
    r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]{0,31}://(?:[^\s'\"<>()/?#\[\]]|\[[^\]\s]*\])*"
    + _TOKEN
    + "*"
)
# A scheme-less `/path?query`, as urllib3 prints after `with url:`. Matched as a whole token so
# the scan is linear on adversarial input.
_BARE_TOKEN_RE = re.compile(r"(?:[^\s'\"<>()\[\]]|\([^\s'\"<>()]*\))+")
# A phone number, bounded on digits only, so it is caught inside a path or file name such as
# `out-5085550123-1001/15085550123_inbound.wav` or glued to letters. Forms: `+` or `%2B` then
# E.164 with separators, 1-NXX-NXX-XXXX or NXX-NXX-XXXX with separators, or any run of 10 or
# more digits (so standalone epoch seconds and millis are masked too). A canonical UUID and a
# `v3:` id are matched first and kept, so a vCon uuid whose last group is all digits survives.
# Ports, IPs, short ids and ISO timestamps have no 10-digit run and no 3-3-4 grouping.
_HEX = "[0-9a-fA-F]"
_PHONE_RE = re.compile(
    rf"(?P<keep>(?<!{_HEX})(?:{_HEX}{{8}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{12}})(?!{_HEX})"
    r"|(?<![\w])v3:[A-Za-z0-9_\-=]+)"
    r"|(?<!\d)(?:(?:\+|%2[Bb])\d[\d\s().\-]{6,17}\d"
    r"|(?:1[\s.\-])?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}"
    r"|(?:%2[Bb])?\d{10,})(?!\d)"
)
_SIP_USER_RE = re.compile(r"^(?:sips?|tel):([^@;>]+)(?:@|;|$)", re.IGNORECASE)


_CALL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_\-]{0,127}")


def call_suffix(call_id: object) -> str:
    """` call=<id>` for the end of a log line, or `` when there is no safe id.

    The call control id is an opaque carrier id, not personal data, and it is what lets a log
    search join this line to the same call's lines on other hosts. Only a plain token passes,
    so an id carrying a space, a newline or anything else cannot forge a log line.
    """
    if isinstance(call_id, str) and _CALL_ID_RE.fullmatch(call_id):
        return f" call={call_id}"
    return ""


def mask_number(number: object) -> str:
    """Reduce a phone number to its last four digits, e.g. `***0101`.

    A SIP or tel URI is masked on its user part only, so a host or port never
    stands in for the digits. Returns `-` for an empty value and `***` when
    fewer than four digits remain.
    """
    if not number:
        return "-"
    text = str(number).strip()
    uri = _SIP_USER_RE.match(text)
    if uri:
        text = uri.group(1)
    elif "@" in text:
        text = text.split("@", 1)[0]
    digits = re.sub(r"\D", "", text)
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


def redact_url(url: object) -> str:
    """Drop the query string, fragment and userinfo from a URL (any scheme)."""
    if not url:
        return ""
    text = str(url).split("#", 1)[0].split("?", 1)[0]
    # Userinfo ends at the last `@` before the first path slash, so a raw `@` in a password is covered.
    match = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*://)([^/]*)(.*)$", text, re.DOTALL)
    if match and "@" in match.group(2):
        host = match.group(2).rsplit("@", 1)[1]
        text = f"{match.group(1)}{host}{match.group(3)}"
    return text


def mask_phone_numbers(text: str) -> str:
    """Replace every phone number in free text with its `mask_number` form."""
    return _PHONE_RE.sub(lambda m: m.group(0) if m.group("keep") else mask_number(m.group(0)), text)


def redact_text_urls(text: object) -> str:
    """Strip queries from every URL in free text, leaving numbers and ids as they are."""
    out = _URL_RE.sub(lambda m: redact_url(m.group(0)), str(text))
    return _BARE_TOKEN_RE.sub(_strip_bare_query, out)


def redact_text(text: object) -> str:
    """Strip queries from every URL, and mask every phone number, in free text.

    Handles full URLs and the scheme-less `/path?query` form that urllib3 prints
    after `with url:`. Numbers (E.164 with or without `+`, 10 to 15 digits, also as
    separators, `%2B`, the user part of a SIP or tel URI) go through `mask_number`.
    """
    return mask_phone_numbers(redact_text_urls(text))


def _strip_bare_query(match: re.Match) -> str:
    """Cut a token at its first `?` or `#` when a `/` comes before it (a path, however glued).

    A `)` that closed a parenthesis opened before the cut is kept: `(/b?x=1)` -> `(/b)`.
    Nested or unbalanced parentheses inside a path are out of scope.
    """
    token = match.group(0)
    for i, ch in enumerate(token):
        if ch in "?#":
            if "/" not in token[:i]:
                return token
            tail = token[i:]
            closers = tail.count(")") - tail.count("(")
            return token[:i] + ")" * max(closers, 0)
    return token


class AccessQueryRedactor(logging.Filter):
    """Strip the query string from the request path in uvicorn access log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0].split("#", 1)[0], *args[3:])
        return True


def uvicorn_log_config() -> dict:
    """Uvicorn's default log config with the access handler filtered by `AccessQueryRedactor`.

    Pass as `log_config=` to `uvicorn.run`. The access log keeps method, path and
    status, and loses the query, which on a webhook route can carry a token.
    """
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["redact_query"] = {"()": f"{__name__}.AccessQueryRedactor"}
    config["handlers"]["access"]["filters"] = ["redact_query"]
    return config
