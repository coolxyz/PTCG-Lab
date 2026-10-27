"""P8-A technical proof gate. Does not grant Windows, signing or release acceptance."""

from hashlib import sha256
import json
from pathlib import Path

REPORT = "releases/p8a-native.json"
CHECKS = {
    "productGUI",
    "onlineGUI",
    "sameOriginSession",
    "practiceSession",
    "persistedDeck",
    "freshLocalSession",
    "immutablePayload",
    "actualWebView",
    "nativeReopen",
    "keychainCrossProcess",
    "explicitIdentityImport",
    "expiredAuthorization",
    "processCompetition",
    "killedOwnerRecovery",
    "parentCrashCleanup",
    "backendCrashRecovery",
    "publicExport",
}


def source_digest(root):
    root = Path(root)
    names = []
    for folder, extensions in (
        ("apps/native-shell/src-tauri", {".rs", ".toml", ".lock", ".json"}),
        ("apps/desktop-api/src", {".py"}),
        ("packages/online-client/src", {".py"}),
        ("packages/platform-services/src", {".ts"}),
        ("scripts/native", {".py", ".txt", ".json"}),
    ):
        names.extend(
            p.relative_to(root).as_posix()
            for p in (root / folder).rglob("*")
            if p.is_file()
            and p.suffix in extensions
            and not {"target", "gen", "__pycache__"}.intersection(p.parts)
        )
    names += [
        "apps/desktop-api/pyproject.toml",
        "apps/desktop-web/src/online/controller.ts",
    ]
    digest = sha256()
    for name in sorted(names):
        digest.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return digest.hexdigest()


def valid(root):
    try:
        root = Path(root)
        report = json.loads((root / REPORT).read_text())
        return (
            report["schema"] == 1
            and report["stage"] == "P8-A"
            and report["status"] == "passed"
            and report["sourceSha256"] == source_digest(root)
            and report["platform"] == "macos-arm64"
            and set(report["checks"]) == CHECKS
            and all(value is True for value in report["checks"].values())
            and report["rulePolicy"] == "current-only-v1"
            and report["pythonTests"]["passed"] >= 70
            and report["pythonTests"]["failed"] == report["pythonTests"]["skipped"] == 0
            and report["rustTests"]["passed"] >= 7
            and report["minimumMacOSActuallyTested"] is False
            and report["windowsAccepted"] is False
            and report["releaseReady"] is False
            and report["p7CapacityAccepted"] is False
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False
