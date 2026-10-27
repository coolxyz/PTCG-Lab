"""Completion needs current, bounded macOS evidence; other platforms stay pending."""

from copy import deepcopy
import json

import pytest
from scripts.native.acceptance import CHECKS, REPORT, source_digest, valid


@pytest.fixture
def evidence(tmp_path):
    # A small synthetic tree verifies gate semantics, not platform acceptance.
    for name in [
        "apps/desktop-api/pyproject.toml",
        "apps/desktop-web/src/online/controller.ts",
    ]:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("synthetic")
    report = {
        "schema": 1,
        "stage": "P8-A",
        "status": "passed",
        "platform": "macos-arm64",
        "sourceSha256": source_digest(tmp_path),
        "rulePolicy": "current-only-v1",
        "checks": dict.fromkeys(CHECKS, True),
        "pythonTests": {"passed": 70, "failed": 0, "skipped": 0},
        "rustTests": {"passed": 7},
        "minimumMacOSActuallyTested": False,
        "windowsAccepted": False,
        "releaseReady": False,
        "p7CapacityAccepted": False,
    }
    path = tmp_path / REPORT
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report))
    return tmp_path, path, report


def test_native_completion_rejects_drift_and_missing_proof(evidence):
    root, path, report = evidence
    assert valid(root)
    for check in CHECKS:
        changed = deepcopy(report)
        changed["checks"][check] = False
        path.write_text(json.dumps(changed))
        assert not valid(root)
    path.write_text(json.dumps(report))
    (root / "apps/desktop-api/pyproject.toml").write_text("unaccepted change")
    assert not valid(root)


@pytest.mark.parametrize(
    "field",
    [
        "windowsAccepted",
        "releaseReady",
        "p7CapacityAccepted",
        "minimumMacOSActuallyTested",
    ],
)
def test_native_proof_cannot_grant_unmeasured_release_or_platform_claims(
    evidence, field
):
    root, path, report = evidence
    report[field] = True
    path.write_text(json.dumps(report))
    assert not valid(root)
