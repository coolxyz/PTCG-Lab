"""Preserve measured P7 history when a separately reaccepted API supersedes it.

This grants no capacity claim for the current source. Historical reports retain
their original image, source, workload and failure status. Current P6-E complete
service acceptance must independently pass before the history can be referenced.
"""

from hashlib import sha256
import json
from pathlib import Path

from scripts.migration.p6e_report import valid as current_service_valid

SNAPSHOT = "releases/p7-measured-p6e-baseline.json"
SNAPSHOT_SHA256 = "554d82660022bfc005ecded6753b842f063cdd18049b5bdc0d9cdcd76ae0fe8e"


def accepted_history(root, expected_source):
    try:
        root = Path(root)
        raw = (root / SNAPSHOT).read_bytes()
        if sha256(raw).hexdigest() != SNAPSHOT_SHA256:
            return None
        report = json.loads(raw)
        if (
            report["serverSourceSha256"] != expected_source
            or report["stage"] != "P6-E"
            or report["status"] != "passed"
            or report["capacityMeasured"] is not False
            or not current_service_valid(root)
        ):
            return None
        return report
    except (OSError, ValueError, KeyError, TypeError):
        return None


MATRIX_SNAPSHOT = "releases/p7-measured-matrix-baseline.json"
MATRIX_SHA256 = "f67bb6ab15506a3c14d0c7db75cd48e5b8f9f6c75d273a7b983dcd943639528c"


def accepted_runtime_history(root, report):
    """Accept only this immutable, unfinished measurement; grant no current capacity."""
    try:
        root = Path(root)
        raw = (root / MATRIX_SNAPSHOT).read_bytes()
        return (
            sha256(raw).hexdigest() == MATRIX_SHA256
            and json.loads(raw) == report
            and report["capacityValidated"] is False
            and report["formalMatrixCompleted"] is False
            and accepted_history(root, report["serverSourceSha256"]) is not None
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False
