"""CON-1100: embedded dialog/attachment bodies must round-trip as base64url.

draft-ietf-vcon-vcon-core-04's Attachment/Dialog Object schema restricts
`encoding` to `"base64url"`, `"json"`, or `"none"` -- plain `"base64"` fails
schema validation. `core.encoding` is the one place that encodes/decodes it;
every builder that inlines audio (`core.base_builder`, and the vapi, pipecat,
and elevenlabs builders, which don't subclass `BaseVconBuilder`) calls into
it rather than rolling its own.
"""

from core.encoding import base64url_decode, base64url_encode


def test_round_trips_arbitrary_bytes():
    original = bytes(range(256))
    assert base64url_decode(base64url_encode(original)) == original


def test_encoding_is_unpadded_and_url_safe():
    # Byte strings chosen so the standard base64 alphabet would emit both
    # "+" and "/", which base64url must render as "-" and "_" instead, and
    # so the raw base64 output would need padding, which base64url must
    # strip per the JWS-style base64url the spec points to (RFC 7515
    # Appendix C / RFC 4648 Section 5).
    data = bytes([0xFB, 0xFF, 0xBF])  # standard b64: "+/+/" -> would include "+" and "/"
    encoded = base64url_encode(data)
    assert "+" not in encoded
    assert "/" not in encoded
    assert "=" not in encoded
    assert base64url_decode(encoded) == data


def test_produces_dash_and_underscore_and_round_trips():
    # Bytes deliberately picked so the standard base64 alphabet emits both
    # "+" and "/" (0xFB 0xFF 0xBF -> "+/+/"), which base64url must render as
    # "-" and "_" instead.
    data = bytes([0xFB, 0xFF, 0xBF, 0xFB, 0xEF, 0xBE])
    encoded = base64url_encode(data)
    assert "-" in encoded
    assert "_" in encoded
    assert base64url_decode(encoded) == data


def test_decode_restores_padding_for_all_remainder_lengths():
    # Unpadded base64 can be 4, 3, or 2 chars long mod 4 depending on the
    # input length; make sure the padding restoration handles each case.
    for length in (1, 2, 3, 4, 5, 6, 7, 8):
        data = bytes(range(length))
        encoded = base64url_encode(data)
        assert base64url_decode(encoded) == data
