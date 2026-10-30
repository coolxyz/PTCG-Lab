"""Fail-closed B4 automation evidence; manual product acceptance stays pending."""

from hashlib import sha256
import json
from pathlib import Path

from scripts.packaging.source_identity import source_digest
from scripts.native.windows_environment import eligible
from scripts.native.windows_report import summarize as python_summary
from scripts.native.windows_shell_check import summarize as rust_summary
from scripts.native.windows_payload_check import verify
from scripts.native.windows_payload import pe_machine
from scripts.public.policy import safe_file

GUI_CHECKS = {
    "actualWebview",
    "sameOriginAuthorization",
    "offlineProfile",
    "singleInstanceFocus",
    "normalExit",
    "sameProfileReopen",
    "deckPersistence",
    "parentPipeEof",
    "ownedBackendCleanup",
}
OWNER_CHECKS = {
    "rustOwnerStart",
    "sameProfileRestart",
    "freshCookie",
    "ownedExit",
    "immutablePayload",
}
PAYLOAD_CHECKS = {
    "productGUI",
    "onlineGUI",
    "sameOriginSession",
    "practiceSession",
    "fiveH2Policies",
    "persistedDeck",
    "freshLocalSession",
    "immutablePayload",
}


def all_checks(value, expected):
    return (
        isinstance(value, dict)
        and set(value) == expected
        and all(v is True for v in value.values())
    )


def collect(run, *, root):
    run, root = Path(run).absolute(), Path(root)
    if any(
        p.is_symlink() or p.is_junction() for p in (run, *run.parents, *run.rglob("*"))
    ):
        raise ValueError("Evidence links forbidden")
    if (run / "FAILED.json").exists() or (run / "REPORT.json").exists():
        raise ValueError("Failed or already finalized run cannot be accepted")
    digest = source_digest(root)
    files = [
        "environment.json",
        "python/report.json",
        "python/results.xml",
        "rust.json",
        "rust.log",
        "gui/report.json",
        "bundle/MANIFEST.json",
        "bundle/BACKEND-PROBE.json",
        "bundle/BACKEND-OWNER-PROBE.json",
    ]

    def load(name):
        return json.loads(safe_file(run, name).read_text(encoding="utf-8"))

    if not eligible(load("environment.json")):
        raise ValueError("Native standard-user Windows x64 evidence required")

    def stage(name, stage_name):
        report = load(name)
        if (
            report.get("schema") != 1
            or report.get("stage") != stage_name
            or report.get("status") != "passed"
            or report.get("sourceSha256") != digest
            or report.get("releaseReady") is not False
        ):
            raise ValueError("Missing, stale or unsuccessful evidence")
        return report

    python = stage("python/report.json", "P8-B-components")
    if python.get("python") != "3.14.10" or python.get("tests") != python_summary(
        safe_file(run, "python/results.xml")
    ):
        raise ValueError("Incomplete bundled Python tests")
    rust = stage("rust.json", "P8-B-shell-components")
    if (
        rust.get("tests")
        != rust_summary(safe_file(run, "rust.log").read_text(encoding="utf-8"))
        or rust.get("nativeShellCompile") != "passed"
        or rust.get("releaseBuild") != "passed"
    ):
        raise ValueError("Incomplete native Rust tests")
    gui = stage("gui/report.json", "P8-B-desktop-smoke")
    exe = safe_file(run, "bundle/PTCG Lab Preview.exe")
    pe_machine(exe)
    executable_sha = sha256(exe.read_bytes()).hexdigest()
    if (
        gui.get("platform") != "windows-x64"
        or gui.get("executableSha256") != executable_sha
        or not all_checks(gui.get("checks"), GUI_CHECKS)
    ):
        raise ValueError("Packaged Windows WebView proof required")
    payload = stage("bundle/BACKEND-PROBE.json", "P8-B-native-payload")
    if (
        not all_checks(payload.get("checks"), PAYLOAD_CHECKS)
        or payload.get("externalWheels") != 37
        or payload.get("internalWheels") != 4
        or not all_checks(load("bundle/BACKEND-OWNER-PROBE.json"), OWNER_CHECKS)
    ):
        raise ValueError("Incomplete bundled runtime or owner proof")
    verify(run / "bundle/backend")
    package = load("bundle/MANIFEST.json")
    from scripts.packaging.common import provenance

    if (
        package.get("kind") != "ptcg-windows-preview"
        or package.get("platform") != "windows-x64"
        or package.get("releaseReady") is not False
        or package.get("source", {}).get("publicSourceSha256")
        != provenance()["publicSourceSha256"]
    ):
        raise ValueError("Stale or invalid package")
    for name, expected in package["files"].items():
        if sha256(safe_file(run / "bundle", name).read_bytes()).hexdigest() != expected:
            raise ValueError("Changed package artifact")
    if {
        p.relative_to(run / "bundle").as_posix()
        for p in (run / "bundle").rglob("*")
        if p.is_file()
    } != set(package["files"]) | {"MANIFEST.json"}:
        raise ValueError("Unexpected package artifact")
    return {
        "schema": 1,
        "stage": "P8-B-B4-automation",
        "status": "automated_passed",
        "platform": "windows-x64",
        "sourceSha256": digest,
        "executableSha256": executable_sha,
        "pythonTests": python["tests"],
        "rustTests": rust["tests"],
        "evidence": {
            name: sha256(safe_file(run, name).read_bytes()).hexdigest()
            for name in files
        },
        "manualAcceptance": {
            name: "pending"
            for name in (
                "visualInteraction",
                "cleanMachine",
                "minimumWindows",
                "fullPractice",
                "twoDeviceNaturalMatch",
                "crashRecovery",
                "identityHandoff",
            )
        },
        "releaseReady": False,
        "p8bComplete": False,
    }
