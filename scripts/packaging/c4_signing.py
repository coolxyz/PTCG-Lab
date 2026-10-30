"""Sign a NEW APP copy, reseal payload/SBOM, then seal the outer APP.

Ad-hoc is the credential-free local test mode. Developer ID is explicit and
never implies notarization or distribution approval. No certificate discovery,
private-key export, upload or Gatekeeper-policy change is performed here.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.packaging.common import new_output, record  # noqa: E402
from scripts.packaging.desktop_audit import audit  # noqa: E402
from scripts.packaging.source_identity import source_digest  # noqa: E402


def commands(app, paths, *, mode="adhoc", identity=None):
    if mode not in {"adhoc", "developer-id"}:
        raise ValueError("Unknown signing mode")
    if mode == "developer-id" and not re.fullmatch(r"[A-Fa-f0-9]{40}", identity or ""):
        raise ValueError("Supply an explicit Developer ID certificate fingerprint")
    if mode == "adhoc" and identity is not None:
        raise ValueError("Ad-hoc mode must not select a certificate")
    flags = ["--timestamp", "--options", "runtime"] if mode == "developer-id" else []
    return [
        [
            "/usr/bin/codesign",
            "--force",
            "--sign",
            identity or "-",
            *flags,
            str(Path(app) / p),
        ]
        for p in sorted(paths, key=lambda x: (-len(Path(x).parts), x))
    ]


def identified(text, team):
    flags = re.search(r"CodeDirectory[^\n]*flags=0x([a-fA-F0-9]+)", text)
    return (
        bool(flags and int(flags[1], 16) & 0x10000)
        and "Authority=Developer ID Application:" in text
        and re.search(r"^TeamIdentifier=" + re.escape(team) + r"$", text, re.M)
        is not None
        and re.search(r"^Timestamp=(?!none$).+", text, re.M) is not None
        and "Signature=adhoc" not in text
    )


def reseal(app, transform):
    """Signing changes bytes; hashes and inventory must precede outer sealing."""
    app = Path(app)
    payload = app / "Contents/Resources/backend"
    path = payload / "manifest.json"
    m = json.loads(path.read_text())
    for name in m["files"]:
        m["files"][name] = sha256((payload / name).read_bytes()).hexdigest()
    path.write_text(json.dumps(m, sort_keys=True) + "\n", encoding="utf-8")
    distribution = app / "Contents/Resources/distribution"
    p = distribution / "COMPONENTS.json"
    inventory = json.loads(p.read_text())
    inventory["payloadManifestSha256"] = sha256(path.read_bytes()).hexdigest()
    p.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    p = distribution / "SBOM.spdx.json"
    sbom = json.loads(p.read_text())
    sbom["documentNamespace"] = (
        "https://spdx.org/spdxdocs/ptcg-"
        + sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    )
    p.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    (distribution / "SIGNING-TRANSFORM.json").write_text(
        json.dumps(transform, indent=2) + "\n", encoding="utf-8"
    )


def sign(app, output, *, expected, mode="adhoc", identity=None, team=None):
    if sys.platform != "darwin":
        raise ValueError("Native macOS required")
    if mode == "developer-id" and not re.fullmatch(r"[A-Z0-9]{10}", team or ""):
        raise ValueError("Supply the expected Developer ID team")
    app = Path(app).absolute()
    platform = json.loads(
        (app / "Contents/Resources/backend/manifest.json").read_text()
    )["platform"]
    arch = {"macos-arm64": "arm64", "macos-x86_64": "x86_64"}.get(platform)
    if arch is None:
        raise ValueError("macOS payload required")
    before = audit(app, architecture=arch)
    if (
        before["appTreeSha256"] != expected
        or before["signature"]["cryptographicVerification"] is not True
    ):
        raise ValueError("Input APP differs from approved audit")
    # Validate modes and paths before creating an output.
    outer_path = "Contents/MacOS/ptcg-native-shell"
    outer_before = next(
        p["sha256"] for p in before["nativeBinaries"] if p["path"] == outer_path
    )
    paths = [p["path"] for p in before["nativeBinaries"] if p["path"] != outer_path]
    commands(app, paths, mode=mode, identity=identity)
    output = new_output(output)
    target = output / "PTCG Lab Preview.app"
    shutil.copytree(app, target)
    changes = []
    for command in commands(target, paths, mode=mode, identity=identity):
        p = Path(command[-1])
        original = sha256(p.read_bytes()).hexdigest()
        subprocess.run(command, check=True, capture_output=True, timeout=120)
        if mode == "developer-id":
            details = subprocess.run(
                ["/usr/bin/codesign", "-dv", "--verbose=4", str(p)],
                capture_output=True,
                text=True,
                check=True,
            ).stderr
            if not identified(details, team):
                raise ValueError("Nested Developer ID verification failed")
        changes.append(
            {
                "path": p.relative_to(target).as_posix(),
                "beforeSha256": original,
                "afterSha256": sha256(p.read_bytes()).hexdigest(),
            }
        )
    transform = {
        "schema": 1,
        "stage": "P8-C4-signing",
        "mode": mode,
        "wrapperSourceSha256": source_digest(ROOT),
        "inputAppTreeSha256": expected,
        "inputBuildSourceSha256": before["packagingSourceSha256"],
        "binaryTransforms": changes,
        "outerExecutable": {
            "path": outer_path,
            "beforeSha256": outer_before,
            "afterHash": "recorded-outside-APP-after-outer-signature",
        },
        "runtimeSourceCodeChanged": False,
        "developerIDExecuted": mode == "developer-id",
        "notarization": "pending",
        "distributionApproved": False,
        "releaseReady": False,
    }
    reseal(target, transform)
    outer = commands(target.parent, [target.name], mode=mode, identity=identity)[0]
    subprocess.run(outer, check=True, capture_output=True, timeout=120)
    result = audit(target, architecture=arch)
    if result["signature"]["cryptographicVerification"] is not True:
        raise ValueError("Outer APP signature verification failed")
    if mode == "developer-id":
        details = subprocess.run(
            ["/usr/bin/codesign", "-dv", "--verbose=4", str(target)],
            capture_output=True,
            text=True,
            check=True,
        ).stderr
        if not identified(details, team):
            raise ValueError("Outer Developer ID verification failed")
    if audit(app, architecture=arch) != before:
        raise ValueError("Input APP changed")
    record(output / "ARTIFACT-AUDIT.json", result)
    # The outer seal changes the main executable's CodeDirectory. Its final
    # digest belongs outside the APP; embedding it would create a hash cycle.
    changes = changes + [
        {
            "path": outer_path,
            "beforeSha256": outer_before,
            "afterSha256": next(
                p["sha256"] for p in result["nativeBinaries"] if p["path"] == outer_path
            ),
        }
    ]
    record(
        output / "SIGNING.json",
        {
            **transform,
            "binaryTransforms": changes,
            "status": "passed",
            "platform": platform,
            "outputAppTreeSha256": result["appTreeSha256"],
            "inputAppUnchanged": True,
        },
    )
    return {"status": "passed", "mode": mode, "releaseReady": False}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--app-tree-sha256", required=True)
    p.add_argument("--mode", choices=["adhoc", "developer-id"], default="adhoc")
    p.add_argument("--identity")
    p.add_argument("--team")
    a = p.parse_args()
    print(
        json.dumps(
            sign(
                a.app,
                a.output,
                expected=a.app_tree_sha256,
                mode=a.mode,
                identity=a.identity,
                team=a.team,
            )
        )
    )
