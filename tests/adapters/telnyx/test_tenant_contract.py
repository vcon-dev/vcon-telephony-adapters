"""Contract: the store's own tenant extractor reads the tenant this builder writes.

vcon-mcp decides which tenant a posted vCon belongs to with `extractTenantFromVCon`
(`src/config/tenant-config.ts`); a vCon it reads no tenant from is the one a tenant-enforcing
writer quarantines. This runs that function, unmodified, on the exact JSON the adapter posts
for real-shaped forwards from both carriers.

It needs a vcon-mcp checkout with its node dependencies installed, so it runs only when
`VCON_MCP_DIR` names one and is skipped otherwise:

    VCON_MCP_DIR=/path/to/vcon-mcp pytest tests/adapters/telnyx/test_tenant_contract.py

The extractor is run with its default configuration (attachment purpose `tenant`, JSON path
`id`). The second test runs vcon-mcp's lawful basis reader (`src/utils/lawful-basis.ts`) the
same way, on the tenant's own basis the adapter builds and on the global one.
"""

import json
import os
import subprocess
import urllib.parse
from pathlib import Path

import pytest

from adapters.telnyx.webhook import TENANT_FIELD

from .tenant_cases import CARRIERS, FORMS, INVALID, REMOTE, TENANT, form_body, post_forward

VCON_MCP_DIR = os.environ.get("VCON_MCP_DIR")

pytestmark = pytest.mark.skipif(
    not VCON_MCP_DIR, reason="set VCON_MCP_DIR to a vcon-mcp checkout to run the real extractor"
)

RUNNER = """\
const config = await import(process.env.VCON_MCP_DIR + "/src/config/tenant-config.ts");
let input = "";
for await (const chunk of process.stdin) input += chunk;
const settings = config.getTenantConfig();
const vcons = JSON.parse(input);
process.stdout.write(JSON.stringify(vcons.map((v) => config.extractTenantFromVCon(v, settings))));
"""


def extract(vcons: list[dict], tmp_path: Path) -> list[str | None]:
    """The tenant vcon-mcp's extractor reads from each vCon, or None."""
    root = Path(VCON_MCP_DIR).resolve()
    tsx = root / "node_modules" / ".bin" / "tsx"
    assert (root / "src" / "config" / "tenant-config.ts").is_file(), f"no extractor in {root}"
    assert tsx.is_file(), f"no {tsx}: run npm ci in the vcon-mcp checkout"
    runner = tmp_path / "extract.mts"
    runner.write_text(RUNNER)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("TENANT_ATTACHMENT_TYPE", "TENANT_JSON_PATH", "CURRENT_TENANT_ID")
    }
    env.update(VCON_MCP_DIR=str(root), RLS_ENABLED="true")
    done = subprocess.run(
        [str(tsx), str(runner)],
        input=json.dumps(vcons),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def wire(vcon) -> dict:
    """What the adapter posts to the conserver: `HttpPoster` sends `vcon.to_json()`."""
    return json.loads(vcon.to_json())


def test_the_store_extractor_reads_the_tenant_from_real_builder_output(tmp_path):
    cases = []
    for carrier in CARRIERS:
        for name, tenant, expected in [
            ("valid", TENANT, TENANT),
            ("absent", None, None),
            ("invalid", INVALID, None),
        ]:
            state = tmp_path / f"{carrier}-{name}"
            state.mkdir()
            vcon = post_forward(form_body(carrier, tenant), state)
            cases.append((f"{carrier}/{name}", wire(vcon), expected))

    # A valid tenant from a peer not trusted as the relay, and two different tenants.
    state = tmp_path / "untrusted"
    state.mkdir()
    vcon = post_forward(form_body("telnyx", TENANT), state, peer=REMOTE)
    cases.append(("telnyx/valid, untrusted peer", wire(vcon), None))
    state = tmp_path / "conflict"
    state.mkdir()
    pairs = [*FORMS["telnyx"], (TENANT_FIELD, "t-00000000000000b2"), (TENANT_FIELD, TENANT)]
    vcon = post_forward(urllib.parse.urlencode(pairs).encode(), state)
    cases.append(("telnyx/two different tenants", wire(vcon), None))

    # Controls, so a reader that returned the same answer for everything would fail: the
    # string-body form the design warns reads as no tenant, and a bare object reads.
    cases.append(
        (
            "control/string body, encoding none",
            {"attachments": [{"purpose": "tenant", "encoding": "none", "body": '{"id": "x"}'}]},
            None,
        )
    )
    cases.append(
        (
            "control/minimal object body",
            {"attachments": [{"purpose": "tenant", "encoding": "json", "body": {"id": "t-x"}}]},
            "t-x",
        )
    )

    got = extract([vcon for _, vcon, _ in cases], tmp_path)

    assert dict(zip([n for n, _, _ in cases], got, strict=True)) == {
        n: expected for n, _, expected in cases
    }


BASIS_RUNNER = """\
const config = await import(process.env.VCON_MCP_DIR + "/src/config/tenant-config.ts");
const basis = await import(process.env.VCON_MCP_DIR + "/src/utils/lawful-basis.ts");
let input = "";
for await (const chunk of process.stdin) input += chunk;
const settings = config.getTenantConfig();
const vcons = JSON.parse(input);
process.stdout.write(JSON.stringify(vcons.map((v) => ({
  tenant: config.extractTenantFromVCon(v, settings),
  has: basis.hasLawfulBasis(v),
  warnings: basis.lawfulBasisWarnings(v),
}))));
"""


def test_the_store_lawful_basis_reader_reads_the_tenant_s_basis(tmp_path):
    """vcon-mcp's own `lawfulBasisWarnings` and `hasLawfulBasis` (src/utils/lawful-basis.ts),
    the warnings its create and ingest routes return, on the adapter's real output. The design
    gives a tenant's basis no expiration and a customer's own entry no proof, so the reader
    says so; a tenant with no basis reads as missing; the no-tenant path is as before."""
    from .basis_cases import CHECKED, CUSTOMER, GLOBAL_ENV, basis_body

    label = "lawful_basis attachment"
    no_expiration = f"{label}: expiration is missing (only legitimate_interests may omit it)"
    no_proof = f"{label}: proof_mechanisms is empty"
    missing = (
        "no lawful_basis attachment: this vCon carries no record of the lawful basis for "
        "processing"
    )
    cases = []
    for name, basis, tenant, post_args, expected in [
        ("customer entered", CUSTOMER, TENANT, {}, [no_expiration, no_proof]),
        ("partner entered, checked", CHECKED, TENANT, {}, [no_expiration]),
        (
            "checked, legitimate interests",
            dict(CHECKED, lawful_basis="legitimate_interests"),
            TENANT,
            {},
            [],
        ),
        ("tenant, no basis fields", None, TENANT, {}, [missing]),
        ("tenant, refused basis", dict(CUSTOMER, consent_record="paper"), TENANT, {}, [missing]),
        ("no tenant: the global basis", CHECKED, None, {}, [no_proof]),
        ("untrusted: the global basis", CHECKED, TENANT, {"peer": REMOTE}, [no_proof]),
    ]:
        state = tmp_path / name.replace(" ", "-").replace(",", "").replace(":", "")
        state.mkdir()
        vcon = post_forward(basis_body("telnyx", basis, tenant), state, **post_args, **GLOBAL_ENV)
        counted = tenant if not post_args else None
        cases.append((name, wire(vcon), {"tenant": counted, "has": expected != [missing]}))
        cases[-1][2]["warnings"] = expected

    root = Path(VCON_MCP_DIR).resolve()
    assert (root / "src" / "utils" / "lawful-basis.ts").is_file(), f"no reader in {root}"
    runner = tmp_path / "basis.mts"
    runner.write_text(BASIS_RUNNER)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("TENANT_ATTACHMENT_TYPE", "TENANT_JSON_PATH", "CURRENT_TENANT_ID")
    }
    env.update(VCON_MCP_DIR=str(root), RLS_ENABLED="true")
    done = subprocess.run(
        [str(root / "node_modules" / ".bin" / "tsx"), str(runner)],
        input=json.dumps([v for _, v, _ in cases]),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)

    assert dict(zip([n for n, _, _ in cases], got, strict=True)) == {
        n: expected for n, _, expected in cases
    }
