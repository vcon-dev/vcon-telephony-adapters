"""Contract: the store's own tenant extractor reads the tenant this builder writes.

vcon-mcp decides which tenant a posted vCon belongs to with `extractTenantFromVCon`
(`src/config/tenant-config.ts`); a vCon it reads no tenant from is the one a tenant-enforcing
writer quarantines. This runs that function, unmodified, on the exact JSON the adapter posts
for real-shaped forwards from both carriers.

It needs a vcon-mcp checkout with its node dependencies installed, so it runs only when
`VCON_MCP_DIR` names one and is skipped otherwise:

    VCON_MCP_DIR=/path/to/vcon-mcp pytest tests/adapters/telnyx/test_tenant_contract.py

The extractor is run with its default configuration (attachment purpose `tenant`, JSON path
`id`).
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from .tenant_cases import CARRIERS, INVALID, TENANT, form_body, post_forward

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
