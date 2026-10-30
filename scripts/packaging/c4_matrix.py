"""Artifact-bound C4 machine matrix; local previews cannot grant clean-machine proof."""

from hashlib import sha256
import json
from pathlib import Path
import platform
import re

from scripts.public.policy import safe_file

TARGETS = {"macos-arm64": "12.0", "macos-x86_64": "12.0", "windows-x64": "10"}
CASES = {
    "installMedia",
    "sameProfileReopen",
    "signatureAndTimestamp",
    "notarizationOrSmartScreen",
    "minimumOS",
    "cleanStandardUser",
    "offlineResources",
    "upgradeAndPairedRollback",
}


def facts():
    machine = platform.machine().lower()
    system = platform.system()
    target = (
        "macos-" + machine
        if system == "Darwin"
        else "windows-x64"
        if system == "Windows" and machine in {"amd64", "x86_64"}
        else "unsupported"
    )
    return {
        "platform": target,
        "osVersion": platform.mac_ver()[0]
        if system == "Darwin"
        else platform.version(),
        "architecture": machine,
        "cleanMachineAttested": False,
        "minimumOSActuallyTested": False,
    }


def template(source):
    return {
        "schema": 1,
        "stage": "P8-C4-machine-matrix",
        "wrapperSourceSha256": source,
        "targets": {
            p: {
                "minimumOS": minimum,
                "cases": {c: {"status": "pending"} for c in sorted(CASES)},
            }
            for p, minimum in TARGETS.items()
        },
        "releaseReady": False,
    }


def validate(matrix, directory):
    try:
        if (
            matrix["schema"] != 1
            or matrix["stage"] != "P8-C4-machine-matrix"
            or matrix["releaseReady"] is not False
            or set(matrix["targets"]) != set(TARGETS)
            or not re.fullmatch(r"[a-f0-9]{64}", matrix["wrapperSourceSha256"])
        ):
            return False
        for target, minimum in TARGETS.items():
            row = matrix["targets"][target]
            if row["minimumOS"] != minimum or set(row["cases"]) != CASES:
                return False
            for case, result in row["cases"].items():
                if result["status"] == "pending":
                    if set(result) != {"status"}:
                        return False
                    continue
                if result["status"] != "passed":
                    return False
                if not re.fullmatch(r"[a-f0-9]{64}", result["artifactSha256"]):
                    return False
                path = safe_file(Path(directory), result["evidence"])
                if path.stat().st_size > 1024 * 1024:
                    return False
                raw = path.read_bytes()
                if sha256(raw).hexdigest() != result["sha256"]:
                    return False
                evidence = json.loads(raw)
                if (
                    evidence["status"] != "passed"
                    or evidence["facts"]["platform"] != target
                    or evidence["wrapperSourceSha256"] != matrix["wrapperSourceSha256"]
                    or evidence["artifactSha256"] != result["artifactSha256"]
                    or case not in evidence["acceptedCases"]
                ):
                    return False
                if (
                    case == "cleanStandardUser"
                    and evidence["facts"]["cleanMachineAttested"] is not True
                ):
                    return False
                if case == "minimumOS" and (
                    evidence["facts"]["minimumOSActuallyTested"] is not True
                    or evidence["facts"]["osVersion"].split(".")[
                        : len(minimum.split("."))
                    ]
                    != minimum.split(".")
                ):
                    return False
        return True
    except (OSError, KeyError, TypeError, ValueError):
        return False
