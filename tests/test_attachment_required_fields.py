"""CON-1083 follow-up: every attachment carries start/party/scope/mediatype.

The official schema (draft-ietf-vcon-vcon-core-05, Attachment Object)
requires `start` and `party` on every attachment, `dialog` or `session`, and
`mediatype` whenever `body` is present. `Vcon.add_tag()` sets `party` and
`session` (but not `start` or `mediatype`) on the tags attachment it creates;
`LawfulBasisConfig.apply()` sets `party`/`session`/`mediatype` itself but not
`start`. `scope_attachments`, called by `BaseVconBuilder.build()`, backfills
the rest for every attachment it emits, regardless of purpose.
"""

from unittest.mock import patch

from adapters.twilio.builder import TwilioRecordingData, TwilioVconBuilder
from core.lawful_basis import LawfulBasisConfig

AUDIO = b"RIFF" + b"\x00" * 4 + b"WAVEfmt not real audio, just deterministic bytes"


def _built_vcon(**builder_kwargs):
    with patch.object(TwilioVconBuilder, "_download_recording", return_value=AUDIO):
        builder = TwilioVconBuilder(recording_format="wav", **builder_kwargs)
        vcon = builder.build(
            TwilioRecordingData(
                {
                    "RecordingSid": "RE1",
                    "From": "+15551234567",
                    "To": "+15559876543",
                    "Direction": "inbound",
                    "RecordingUrl": "https://api.twilio.com/recordings/RE1",
                }
            )
        )
        assert vcon is not None, "builder returned None"
        return vcon


def test_every_attachment_carries_start_party_session_and_mediatype():
    """Both the tags attachment and the lawful_basis attachment qualify."""
    vcon = _built_vcon(
        lawful_basis=LawfulBasisConfig(lawful_basis="consent", purposes=["recording"])
    )

    attachments = vcon.to_dict()["attachments"]
    assert len(attachments) == 2, "expected a tags attachment and a lawful_basis attachment"

    for attachment in attachments:
        assert "start" in attachment, f"{attachment.get('purpose')} attachment has no start"
        assert "party" in attachment, f"{attachment.get('purpose')} attachment has no party"
        assert attachment.get("session") in (
            0,
            [0],
        ), f"{attachment.get('purpose')} attachment is not scoped to the call's Session"
        assert attachment.get("body") is not None, f"{attachment.get('purpose')} has no body"
        assert attachment.get("mediatype") == "application/json", (
            f"{attachment.get('purpose')} attachment has no mediatype"
        )


def test_backfilled_start_matches_created_at():
    vcon = _built_vcon()

    tags_attachment = next(a for a in vcon.to_dict()["attachments"] if a["purpose"] == "tags")
    assert tags_attachment["start"] == vcon.created_at


def test_lawful_basis_attachment_keeps_its_own_party_and_session():
    """LawfulBasisConfig.apply() already sets party/session to 0 itself; the
    backfill in BaseVconBuilder.build() must not need to (and, via
    setdefault, does not) touch them."""
    vcon = _built_vcon(
        lawful_basis=LawfulBasisConfig(lawful_basis="consent", purposes=["recording"])
    )

    lawful_basis_attachment = next(
        a for a in vcon.to_dict()["attachments"] if a["purpose"] == "lawful_basis"
    )
    assert lawful_basis_attachment["party"] == 0
    assert lawful_basis_attachment["session"] == 0
    assert "dialog" not in lawful_basis_attachment
    assert lawful_basis_attachment["start"] == vcon.created_at
    assert lawful_basis_attachment["mediatype"] == "application/json"
