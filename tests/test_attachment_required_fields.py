"""CON-1083 follow-up: every attachment carries start/party/dialog.

The official schema (draft-ietf-vcon-vcon-core, Attachment Object) requires
`start`, `party`, and `dialog` on every attachment. `Vcon.add_tag()` already
sets `party`/`dialog` (but not `start`) on the tags attachment it creates;
`LawfulBasisConfig.apply()` sets all three itself. Nothing backfilled
`start` for the tags attachment until `BaseVconBuilder.build()` started
doing it for every attachment it emits, regardless of purpose.
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


def test_every_attachment_carries_start_party_and_dialog():
    """Both the tags attachment and the lawful_basis attachment qualify."""
    vcon = _built_vcon(
        lawful_basis=LawfulBasisConfig(lawful_basis="consent", purposes=["recording"])
    )

    attachments = vcon.to_dict()["attachments"]
    assert len(attachments) == 2, "expected a tags attachment and a lawful_basis attachment"

    for attachment in attachments:
        assert "start" in attachment, f"{attachment.get('purpose')} attachment has no start"
        assert "party" in attachment, f"{attachment.get('purpose')} attachment has no party"
        assert "dialog" in attachment, f"{attachment.get('purpose')} attachment has no dialog"


def test_backfilled_start_matches_created_at():
    vcon = _built_vcon()

    tags_attachment = next(a for a in vcon.to_dict()["attachments"] if a["purpose"] == "tags")
    assert tags_attachment["start"] == vcon.created_at


def test_lawful_basis_attachment_keeps_its_own_party_and_dialog():
    """LawfulBasisConfig.apply() already sets party/dialog to 0 itself; the
    backfill in BaseVconBuilder.build() must not need to (and, via
    setdefault, does not) touch them."""
    vcon = _built_vcon(
        lawful_basis=LawfulBasisConfig(lawful_basis="consent", purposes=["recording"])
    )

    lawful_basis_attachment = next(
        a for a in vcon.to_dict()["attachments"] if a["purpose"] == "lawful_basis"
    )
    assert lawful_basis_attachment["party"] == 0
    assert lawful_basis_attachment["dialog"] == 0
    assert lawful_basis_attachment["start"] == vcon.created_at
