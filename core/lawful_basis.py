"""Record why a deployment is entitled to hold a recording.

Per draft-howe-vcon-lawful-basis. Until now no adapter in this monorepo emitted
one: `BaseVconBuilder` wrote no attachments at all, so every vCon it produced
asserted no legal basis for its own existence.

**A basis is never invented.** It is emitted only where a deployment has
explicitly configured one, because a fabricated consent record is worse than a
missing one: it asserts something about a data subject that nobody established.
Unconfigured means the attachment is absent and the builder says so loudly.

## On the attachment shape

Two shapes exist in the wild and they are not interchangeable:

* `purpose: "lawful_basis"` — what `vcon-lib` emits, finds, and validates. Used
  here.
* `type: "lawful_basis"` — the older form, still in the draft's own examples and
  still emitted by `vcon-dev/vcon-siprec-adapter`. `conserver-link-check-consent`
  accepts it for backward compatibility.

The legacy form actively breaks two things, which is why this delegates to the
library rather than hand-rolling it:

1. `Vcon.is_valid()` rejects it — every attachment must carry `purpose`.
2. `Vcon.add_tag()` raises `KeyError` afterwards, because
   `find_attachment_by_purpose` does `a["purpose"]` rather than
   `a.get("purpose")`, so one purpose-less attachment poisons all later tagging.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from vcon import Vcon

logger = logging.getLogger(__name__)

# The six GDPR Article 6 bases. The draft constrains the field to these.
VALID_LAWFUL_BASES = frozenset(
    {
        "consent",
        "contract",
        "legal_obligation",
        "vital_interests",
        "public_task",
        "legitimate_interests",
    }
)

DEFAULT_PURPOSES = ("recording",)


class LawfulBasisConfig:
    """How this deployment justifies holding the recording.

    Disabled unless a basis is explicitly set. There is deliberately no default:
    choosing one would be asserting a legal position on a deployment's behalf.
    """

    def __init__(
        self,
        lawful_basis: str | None = None,
        purposes: Iterable[str] = DEFAULT_PURPOSES,
        expiration: str | None = None,
        justification: str | None = None,
    ):
        self.lawful_basis = (lawful_basis or "").strip() or None
        self.purposes = [p.strip() for p in purposes if p and p.strip()]
        self.expiration = expiration
        self.justification = justification

        if self.lawful_basis and self.lawful_basis not in VALID_LAWFUL_BASES:
            raise ValueError(
                f"LAWFUL_BASIS={self.lawful_basis!r} is not one of " f"{sorted(VALID_LAWFUL_BASES)}"
            )
        if self.lawful_basis and not self.purposes:
            raise ValueError("LAWFUL_BASIS is set but LAWFUL_BASIS_PURPOSES is empty")

    @property
    def enabled(self) -> bool:
        return bool(self.lawful_basis)

    def purpose_grants(self, granted_at: str) -> list[dict[str, Any]]:
        return [
            {"purpose": purpose, "granted": True, "granted_at": granted_at}
            for purpose in self.purposes
        ]

    def apply(self, vcon: Vcon, party_index: int = 0, dialog_index: int = 0) -> bool:
        """Attach the configured basis. Returns False when none is configured.

        Delegates to `vcon-lib`, which owns the shape its own validator and
        finder expect. Under draft-ietf-vcon-vcon-core-04 §2.3.2 (CDDL
        `body: any`), `body` for `encoding: "json"` is the JSON value itself,
        not a `json.dumps` string — so vcon-lib 0.9.6's object body is
        correct as-is and is left untouched. (An earlier revision of this
        method stringified it against a stale schema fork that typed `body`
        as a string; that was wrong for -04 and has been reverted.)

        Every adapter here produces exactly one dialog and references party 0
        as the recording's subject, so `party`/`dialog` default to 0 rather
        than being left off. `mediatype` is required whenever `body` is
        present (-04's Attachment Object); vcon-lib does not set it, so it is
        added here.
        """
        if not self.enabled:
            return False

        from datetime import datetime, timezone

        granted_at = datetime.now(timezone.utc).isoformat()

        vcon.add_lawful_basis_attachment(
            lawful_basis=self.lawful_basis,
            expiration=self.expiration,
            purpose_grants=self.purpose_grants(granted_at),
            party_index=party_index,
            dialog_index=dialog_index,
            **self._body_extras(),
        )

        attachment = vcon.vcon_dict["attachments"][-1]
        attachment.setdefault("mediatype", "application/json")

        return True

    def _body_extras(self) -> dict[str, Any]:
        """Optional body fields, passed through to the library's attachment model."""
        # `justification` is not a first-class field on the library's
        # attachment model, but `metadata` is its extension point. The SIPREC
        # adapter puts justification at the body's top level; that shape does
        # not round-trip through vcon-lib, so it goes here instead.
        if self.justification:
            return {"metadata": {"justification": self.justification}}
        return {}


# ---- A tenant's own basis, relayed per call ------------------------------------------------
#
# A relaying receiver that knows the call's tenant sends that tenant's basis record beside it,
# one field per key the record holds. The keys are the record's own; a null field is absent.
RELAYED_BASIS_KEYS = (
    "lawful_basis",
    "justification",
    "consent_record",
    "chosen_by",
    "chosen_at",
    "entered_by",
    "evidence",
    "checked_by",
    "checked_at",
)

# How the tenant's consent is recorded, and who entered the basis: the record's own sets.
CONSENT_RECORDS = frozenset({"off", "jlinc"})
ENTERED_BY = frozenset({"customer", "partner", "staff"})

# Stated in every basis. A partner or staff entry also needs the customer's signed document and
# the staff check of it; a customer's own entry needs neither.
_ALWAYS_REQUIRED = ("lawful_basis", "justification", "consent_record", "chosen_by", "entered_by")
_CHECKED_ENTRY_REQUIRED = ("evidence", "checked_by", "checked_at")


class RelayedBasisError(ValueError):
    """A relayed basis that cannot be attached. `reason` names no value."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _zoned_time(value: str) -> bool:
    """True for an ISO 8601 time with a zone, parsed as vcon-lib parses a proof's timestamp."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


class RelayedLawfulBasis(LawfulBasisConfig):
    """One tenant's own basis, built from the fields a trusted relay sent with the call.

    Never mixed with the deployment's global basis: the basis and justification are the
    tenant's, purposes are the deployment's `LAWFUL_BASIS_PURPOSES` (the same for every
    tenant), and there is no expiration. The other seven fields go in `metadata` under their
    own keys. A `signed_document` proof is added only for a partner or staff entry, which
    always carries the customer's signed document and the staff check of it; a basis the
    customer entered itself records no proof.

    `from_fields` refuses anything invalid or partial, so nothing is ever filled in.
    """

    def __init__(self, fields: dict[str, str], purposes: Iterable[str]):
        super().__init__(
            lawful_basis=fields["lawful_basis"],
            purposes=purposes,
            expiration=None,
            justification=fields["justification"],
        )
        # Every field but the basis itself, justification first, as `apply` has always put it.
        self.metadata = {k: fields[k] for k in RELAYED_BASIS_KEYS[1:] if k in fields}
        self.proof = None
        if fields["entered_by"] != "customer":
            self.proof = {
                "timestamp": fields["checked_at"],
                "proof_data": {"document": fields["evidence"], "checked_by": fields["checked_by"]},
            }

    @classmethod
    def from_fields(
        cls, relayed: dict[str, Iterable[Any]], purposes: Iterable[str]
    ) -> RelayedLawfulBasis:
        """The tenant's basis from every relayed value per key, or `RelayedBasisError`.

        Refused: a key the relay never sends; a key with two different values; a value that is
        not text or is blank; a basis outside the six, a consent record outside `off` and
        `jlinc`, or an entry outside customer, partner and staff; a required field missing, or
        a partner or staff entry without its document and check; a check without its time or
        a time without its checker; a check time with no zone; and no configured purposes.
        """
        fields: dict[str, str] = {}
        for key, values in relayed.items():
            if key not in RELAYED_BASIS_KEYS:
                raise RelayedBasisError("a basis field the relay never sends")
            values = list(values)
            if not values:
                raise RelayedBasisError("a blank basis value")
            if len({repr(v) for v in values}) > 1:
                raise RelayedBasisError("a basis field with more than one value")
            value = values[-1]
            if not isinstance(value, str):
                raise RelayedBasisError("a basis value that is not text")
            if not value.strip():
                raise RelayedBasisError("a blank basis value")
            fields[key] = value

        required = _ALWAYS_REQUIRED
        if fields.get("entered_by") in ENTERED_BY - {"customer"}:
            required += _CHECKED_ENTRY_REQUIRED
        if any(key not in fields for key in required):
            raise RelayedBasisError("the basis is partial")
        if fields["lawful_basis"] not in VALID_LAWFUL_BASES:
            raise RelayedBasisError("lawful_basis is not one of the six bases")
        if fields["consent_record"] not in CONSENT_RECORDS:
            raise RelayedBasisError("consent_record is not off or jlinc")
        if fields["entered_by"] not in ENTERED_BY:
            raise RelayedBasisError("entered_by is not customer, partner or staff")
        if ("checked_by" in fields) != ("checked_at" in fields):
            raise RelayedBasisError("a check without both checked_by and checked_at")
        if "checked_at" in fields and not _zoned_time(fields["checked_at"]):
            raise RelayedBasisError("checked_at is not a time with a zone")

        purposes = [p.strip() for p in purposes if p and p.strip()]
        if not purposes:
            raise RelayedBasisError("LAWFUL_BASIS_PURPOSES is empty")
        return cls(fields, purposes)

    def _body_extras(self) -> dict[str, Any]:
        from vcon.extensions.lawful_basis.attachment import ProofMechanism, ProofType

        extras: dict[str, Any] = {"metadata": dict(self.metadata)}
        if self.proof is not None:
            extras["proof_mechanisms"] = [
                ProofMechanism(ProofType.SIGNED_DOCUMENT, **self.proof),
            ]
        return extras
