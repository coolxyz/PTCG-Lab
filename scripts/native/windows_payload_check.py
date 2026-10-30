"""Execute the actual bundled Windows interpreter; GUI acceptance stays separate."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.packaging.source_identity import source_digest  # noqa: E402
from scripts.public.policy import safe_file  # noqa: E402


def verify(payload):
    payload = Path(payload).absolute()
    if any(p.is_symlink() or p.is_junction() for p in [payload, *payload.rglob("*")]):
        raise ValueError("Payload links are forbidden")
    manifest = json.loads(safe_file(payload, "manifest.json").read_text())
    if (
        set(manifest) != {"schema", "platform", "python", "files"}
        or manifest["schema"] != 1
        or manifest["platform"] != "windows-x64"
        or manifest["python"] != "python/python.exe"
        or not 1 <= len(manifest["files"]) <= 20000
        or not {"python/python.exe", "bootstrap.py", "BUILD-SOURCE.json"}
        <= set(manifest["files"])
    ):
        raise ValueError("Invalid Windows payload")
    for name, digest in manifest["files"].items():
        if sha256(safe_file(payload, name).read_bytes()).hexdigest() != digest:
            raise ValueError("Changed payload file")
    if {
        p.relative_to(payload).as_posix() for p in payload.rglob("*") if p.is_file()
    } != set(manifest["files"]) | {"manifest.json"}:
        raise ValueError("Unexpected payload file")
    source = json.loads(safe_file(payload, "BUILD-SOURCE.json").read_text())
    if source["nativeSourceSha256"] != source_digest(ROOT):
        raise ValueError("Payload must be rebuilt from current native sources")
    return payload


def check(payload):
    if (
        sys.platform != "win32"
        or os.name != "nt"
        or platform.machine().lower() not in {"amd64", "x86_64"}
    ):
        raise ValueError("Run the Windows payload on native Windows x64")
    payload = verify(payload)
    python = payload / "python/python.exe"
    code = "import sys,json,importlib.metadata as m; import win32crypt,win32security,win32file,cryptography,PIL,pydantic_core,mwparserfromhell; from mwparserfromhell import parser; assert sys.version_info[:3]==(3,14,10); assert not parser.use_c; assert str(mwparserfromhell.parse('{{name|value=中文}}').filter_templates()[0].get('value').value)=='中文'; print(json.dumps({d.metadata['Name']:d.version for d in m.distributions()}))"
    installed = json.loads(
        subprocess.check_output(
            [str(python), "-I", "-B", "-X", "utf8", "-c", code],
            text=True,
            encoding="utf-8",
        )
    )
    import tomllib

    expected = json.loads(
        (ROOT / "scripts/native/windows-wheel-lock.json").read_text()
    )["wheels"]
    for wheel in expected:
        if installed.get(wheel["name"]) != wheel["version"]:
            raise ValueError("Installed external closure differs from lock")
    from scripts.native.layout import PROJECTS

    for project in PROJECTS:
        package = tomllib.loads((ROOT / project / "pyproject.toml").read_text())[
            "project"
        ]
        if installed.get(package["name"]) != package["version"]:
            raise ValueError("Installed product version differs from source")
    if {n.lower().replace("_", "-") for n in installed} != {
        w["name"].lower().replace("_", "-") for w in expected
    } | {
        tomllib.loads((ROOT / p / "pyproject.toml").read_text())["project"]["name"]
        .lower()
        .replace("_", "-")
        for p in PROJECTS
    }:
        raise ValueError("Unexpected installed distribution")
    report = json.loads(
        subprocess.check_output(
            [
                str(python),
                "-I",
                "-B",
                "-X",
                "utf8",
                str(ROOT / "scripts/native/probe.py"),
                str(payload),
                "--all-strategies",
            ],
            text=True,
            encoding="utf-8",
        )
    )
    if len(report["checks"]) != 8 or not all(report["checks"].values()):
        raise ValueError("Native payload probe incomplete")
    return {
        "schema": 1,
        "stage": "P8-B-native-payload",
        "status": "passed",
        "sourceSha256": source_digest(ROOT),
        "checks": report["checks"],
        "externalWheels": len(expected),
        "internalWheels": len(PROJECTS),
        "nativeGuiAccepted": False,
        "releaseReady": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = check(args.payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        "Native Windows payload checks passed; interactive desktop acceptance remains pending"
    )
