"""Relayed tenant bases for the lawful basis tests, and the goldens for the no-tenant path.

The relaying receiver sends a tenant's lawful basis as `Annotation-basis_<key>` form fields,
one per field the tenant's basis record holds, straight before `Annotation-tenant`, which stays
last. A null field is absent, never empty. Every value here is synthetic.

The files under `fixtures/tenant-basis/` are the normalized wire JSON the builder produced for
forms carrying basis fields before the tenant basis existed, at 4e2bfa2 (M16): a basis with no
tenant, with an invalid tenant, with a valid tenant from an untrusted peer, and with two
different tenants. On each of those the adapter must still apply its global basis, byte for
byte as before. To re-check them, check out that commit's `adapters/` and `core/` and run:

    python -m tests.adapters.telnyx.basis_cases --write-goldens
"""

from __future__ import annotations

import sys
import tempfile
import urllib.parse
from pathlib import Path

from .tenant_cases import CARRIERS, FORMS, INVALID, REMOTE, TENANT, normalized, post_forward

GOLDENS = Path(__file__).parent / "fixtures" / "tenant-basis"

BASIS_PREFIX = "Annotation-basis_"

# The order the receiver sends them in.
KEYS = (
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

# A basis the customer entered itself: no document, no check.
CUSTOMER = {
    "lawful_basis": "contract",
    "justification": "Synthetic: recordings serve the synthetic service contract.",
    "consent_record": "off",
    "chosen_by": "synthetic customer admin",
    "chosen_at": "2026-11-01T09:00:00Z",
    "entered_by": "customer",
}

# A basis a partner entered from the customer's signed choice, which staff checked.
CHECKED = {
    "lawful_basis": "consent",
    "justification": "Synthetic: the caller agreed to recording in the synthetic signup.",
    "consent_record": "jlinc",
    "chosen_by": "synthetic customer admin",
    "chosen_at": "2026-11-01T09:00:00Z",
    "entered_by": "partner",
    "evidence": "doc-00000000000000d1",
    "checked_by": "synthetic staff admin",
    "checked_at": "2026-11-02T10:00:00Z",
}

# The adapter's own global basis in these tests: different from every tenant basis above in
# every field, so any value of it that reached a tenant's attachment would show.
GLOBAL_ENV = {
    "LAWFUL_BASIS": "legitimate_interests",
    "LAWFUL_BASIS_JUSTIFICATION": "Synthetic global justification.",
    "LAWFUL_BASIS_EXPIRATION": "2099-01-01T00:00:00Z",
    "LAWFUL_BASIS_PURPOSES": "recording,transcription",
}


def basis_pairs(basis: dict[str, str]) -> list[tuple[str, str]]:
    """The basis fields as the receiver sends them: known keys in its order, then any others."""
    known = [(BASIS_PREFIX + k, basis[k]) for k in KEYS if k in basis]
    return known + [(BASIS_PREFIX + k, v) for k, v in basis.items() if k not in KEYS]


def basis_body(
    carrier: str,
    basis: dict[str, str] | list[tuple[str, str]] | None,
    tenant: str | list[str] | None = TENANT,
) -> bytes:
    """The urlencoded forward: the carrier's fields, the basis fields, then the tenant last.

    `basis` may be a list of (key, value) pairs, unprefixed, to send a field twice or out of
    order. `tenant` may be a list to send the tenant field more than once.
    """
    pairs = list(FORMS[carrier])
    if isinstance(basis, list):
        pairs += [(BASIS_PREFIX + k, v) for k, v in basis]
    elif basis is not None:
        pairs += basis_pairs(basis)
    tenants = tenant if isinstance(tenant, list) else [] if tenant is None else [tenant]
    pairs += [("Annotation-tenant", t) for t in tenants]
    return urllib.parse.urlencode(pairs).encode()


# Golden case -> (form args, post_forward args). Every one is a request whose tenant does not
# count, so the vCon must carry the global basis exactly as M16 built it.
NO_TENANT_CASES: dict[str, tuple[dict, dict]] = {
    "absent": ({"tenant": None}, {}),
    "invalid": ({"tenant": INVALID}, {}),
    "untrusted": ({"tenant": TENANT}, {"peer": REMOTE}),
    "conflicting": ({"tenant": ["t-00000000000000b2", TENANT]}, {}),
}


def golden_path(carrier: str, case: str) -> Path:
    return GOLDENS / f"{carrier}-{case}.json"


def post_no_tenant_case(carrier: str, case: str, state_dir, **env):
    form_args, post_args = NO_TENANT_CASES[case]
    return post_forward(
        basis_body(carrier, CHECKED, **form_args), state_dir, **post_args, **GLOBAL_ENV, **env
    )


def write_goldens() -> None:
    GOLDENS.mkdir(parents=True, exist_ok=True)
    for carrier in CARRIERS:
        for case in NO_TENANT_CASES:
            with tempfile.TemporaryDirectory() as state_dir:
                vcon = post_no_tenant_case(carrier, case, state_dir)
            golden_path(carrier, case).write_text(normalized(vcon) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] != ["--write-goldens"]:
        sys.exit("usage: python -m tests.adapters.telnyx.basis_cases --write-goldens")
    write_goldens()
