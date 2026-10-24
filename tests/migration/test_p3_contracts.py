"""The exact invented browser DTOs must satisfy the P2-D wire/visibility contract."""

import json
from pathlib import Path
import subprocess
from ptcg_protocol.validation import validate

ROOT = Path(__file__).resolve().parents[2]


def test_browser_scenarios_follow_authoritative_protocol():
    result = subprocess.run(
        ["node", "scripts/migration/p3_fixture_export.mjs"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    cases = json.loads(result.stdout)
    assert len(cases) == 14
    for case in cases:
        assert validate(case["kind"], case["value"]) == case["value"]
