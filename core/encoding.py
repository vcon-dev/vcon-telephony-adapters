"""Shared base64url helpers for inline vCon dialog/attachment bodies.

draft-ietf-vcon-vcon-core-04 requires an inline `body`'s `encoding` to be one
of "base64url", "json", or "none" -- plain "base64" is not a valid value.
"base64url" here matches the JWS-style Base64Url encoding the spec points to
(Section 2 of [JWS], i.e. RFC 7515 Appendix C / RFC 4648 Section 5): the
URL-safe alphabet with "=" padding stripped.
"""

import base64


def base64url_encode(data: bytes) -> str:
    """Encode bytes as unpadded, URL-safe base64 (draft-04 `encoding: base64url`)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def base64url_decode(value: str) -> bytes:
    """Decode a draft-04 `base64url` string, restoring the padding it dropped."""
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded)
