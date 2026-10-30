"""Prepare, never cross-execute, a Windows per-user NSIS/signing contract.

The configuration remains disabled until B4 and native C3 migration are accepted.
WebView2 is an explicit preinstalled prerequisite; no unpinned download happens.
"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.native.windows_payload_check import verify  # noqa: E402
from scripts.native.windows_payload import pe_machine  # noqa: E402
from scripts.packaging.common import new_output, record, write, file_hash  # noqa: E402
from scripts.packaging.source_identity import source_digest  # noqa: E402

HOOK = r"""!macro NSIS_HOOK_PREINSTALL
  ; Do not modify existing profile data while native C3 migration is pending.
  IfFileExists "$LOCALAPPDATA\org.ptcglab.desktop.preview\app.sqlite" 0 +3
  MessageBox MB_OK|MB_ICONSTOP "Close PTCG Lab and use the approved C3 migration tool before upgrading."
  Abort
  IfFileExists "$LOCALAPPDATA\org.ptcglab.desktop.preview\.desktop-maintenance.json" 0 +3
  MessageBox MB_OK|MB_ICONSTOP "A previous migration requires recovery. Existing data was preserved."
  Abort
!macroend
!macro NSIS_HOOK_PREUNINSTALL
  ; User data is outside INSTDIR and must never be removed by this installer.
!macroend
"""


def configuration(payload, hooks):
    return {
        "identifier": "org.ptcglab.desktop.preview",
        "version": "0.8.0",
        "productName": "PTCG Lab Preview",
        "build": {"windows": {"staticVCRuntime": True}},
        "bundle": {
            "active": False,
            "targets": ["nsis"],
            "resources": {str(payload) + "/": "backend/"},
            "windows": {
                "allowDowngrades": False,
                "webviewInstallMode": {"type": "skip"},
                "nsis": {
                    "installMode": "currentUser",
                    "languages": ["SimpChinese", "English"],
                    "displayLanguageSelector": True,
                    "installerHooks": str(hooks),
                },
            },
        },
    }


def prepare(payload, output):
    payload = verify(payload)
    output = new_output(output)
    config = configuration(payload, output / "migration-guard.nsh")
    import jsonschema

    schema = json.loads(
        (ROOT / "node_modules/@tauri-apps/cli/config.schema.json").read_text()
    )
    jsonschema.validate(config, schema)
    files = json.loads((payload / "manifest.json").read_text())["files"]
    binaries = []
    for name in files:
        p = payload / name
        if p.suffix.lower() in {".dll", ".pyd", ".exe"}:
            pe_machine(p)
            binaries.append({"path": name, "sha256": files[name]})
    record(output / "tauri-nsis-preparation.json", config)
    write(output / "migration-guard.nsh", HOOK)
    # Native operator template: certificate lookup is CurrentUser only, no PFX,
    # password, machine store change, policy change, or signature bypass.
    write(
        output / "Verify-Authenticode.ps1",
        r"""param(
  [Parameter(Mandatory=$true)][string]$File,
  [Parameter(Mandatory=$true)][string]$ExpectedThumbprint
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Native Windows required' }
if ($ExpectedThumbprint -notmatch '^[0-9A-Fa-f]{40}$') { throw 'Explicit certificate thumbprint required' }
$signature = Get-AuthenticodeSignature -LiteralPath $File
if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
    $signature.SignerCertificate.Thumbprint -ne $ExpectedThumbprint -or
    $null -eq $signature.TimeStamperCertificate) { throw 'Authenticode/timestamp verification failed' }
Write-Output '{"signature":"valid","releaseReady":false}'
""",
    )
    report = {
        "schema": 1,
        "stage": "P8-C4-Windows-preparation",
        "wrapperSourceSha256": source_digest(ROOT),
        "status": "prepared-blocked",
        "installerEnabled": False,
        "nativeExecution": "pending",
        "B4Accepted": False,
        "C3WindowsMigrationAccepted": False,
        "webview2": "preinstalled-required-not-verified",
        "nsisSchemaValidated": True,
        "payloadManifestSha256": file_hash(payload / "manifest.json"),
        "peFilesAMD64": binaries,
        "signingOrder": [
            "verify-approved-native-inputs",
            "sign-nested-PE-with-explicit-certificate-and-RFC3161",
            "reseal-payload-manifest-and-SBOM",
            "sign-native-shell",
            "build-currentUser-NSIS",
            "sign-installer",
            "verify-Authenticode-and-timestamp",
            "standard-user-clean-machine-acceptance",
        ],
        "releaseReady": False,
    }
    record(output / "WINDOWS-PREPARATION.json", report)
    return {
        "status": report["status"],
        "peFilesAMD64": len(binaries),
        "installerEnabled": False,
        "releaseReady": False,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--payload", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(prepare(a.payload, a.output)))
