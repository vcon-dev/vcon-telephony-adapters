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

    def apply(self, vcon: Vcon, party_index: int | None = None) -> bool:
        """Attach the configured basis. Returns False when none is configured.

        Delegates to `vcon-lib`, which owns the shape its own validator and
        finder expect.
        """
        if not self.enabled:
            return False

        from datetime import datetime, timezone

        granted_at = datetime.now(timezone.utc).isoformat()
        # `justification` is not a first-class field on the library's
        # attachment model, but `metadata` is its extension point. The SIPREC
        # adapter puts justification at the body's top level; that shape does
        # not round-trip through vcon-lib, so it goes here instead.
        extra: dict[str, Any] = {}
        if self.justification:
            extra["metadata"] = {"justification": self.justification}

        vcon.add_lawful_basis_attachment(
            lawful_basis=self.lawful_basis,
            expiration=self.expiration,
            purpose_grants=self.purpose_grants(granted_at),
            party_index=party_index,
            **extra,
        )
        return True
