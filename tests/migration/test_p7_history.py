# ruff: noqa: F811
"""A newer functional acceptance must not relabel old measurements as current."""

import json

from scripts.migration.p7_baseline import accepted_history, SNAPSHOT, MATRIX_SNAPSHOT
from scripts.migration.p7a_report import valid as pilot_valid
from scripts.migration.p7b_report import valid as matrix_valid
from tests.migration.test_boundaries import scaffold  # noqa: F401


def test_historical_capacity_evidence_requires_explicit_mode(scaffold):
    assert not pilot_valid(scaffold)
    assert not matrix_valid(scaffold)
    assert pilot_valid(scaffold, historical=True)
    assert matrix_valid(scaffold, historical=True)
    assert (
        json.loads((scaffold / "releases/p7b-matrix.json").read_text())[
            "capacityValidated"
        ]
        is False
    )


def test_snapshot_tamper_or_current_service_drift_rejects_history(scaffold):
    path = scaffold / SNAPSHOT
    raw = path.read_bytes()
    path.write_bytes(raw + b" ")
    assert not matrix_valid(scaffold, historical=True)
    path.write_bytes(raw)
    source = scaffold / "apps/online-server/src/ptcg_online_server/api.py"
    source.write_text(source.read_text() + "\n# unaccepted source change\n")
    assert not matrix_valid(scaffold, historical=True)
    assert not pilot_valid(scaffold, historical=True)


def test_history_cannot_approve_an_arbitrary_measured_source(scaffold):
    assert accepted_history(scaffold, "0" * 64) is None


def test_runtime_snapshot_cannot_be_tampered_or_relabelled(scaffold):
    path = scaffold / MATRIX_SNAPSHOT
    raw = path.read_bytes()
    path.write_bytes(raw + b" ")
    assert not matrix_valid(scaffold, historical=True)
    path.write_bytes(raw)
    report_path = scaffold / "releases/p7b-matrix.json"
    report = json.loads(report_path.read_text())
    report["runtimeSourceSha256"] = "0" * 64
    report_path.write_text(json.dumps(report))
    assert not matrix_valid(scaffold, historical=True)
