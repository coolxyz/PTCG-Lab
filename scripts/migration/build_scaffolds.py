# ruff: noqa: E402
"""Build inactive projects in scratch directories and verify portable artifacts."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.public.policy import inspect, safe_file, safe_name


def check_members(members, allowed):
    for name, content in members:
        if not safe_name(name) or name not in allowed:
            raise ValueError("Unexpected build artifact member: " + name)
        findings = inspect("src/" + name, content)
        if findings:
            raise ValueError("Build artifact blocked [" + ",".join(findings) + "]")


def build(output, root=ROOT, wheelhouse=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    registry = json.loads((root / "releases/projects.json").read_text())
    results = []
    with tempfile.TemporaryDirectory(prefix="ptcg-build-") as temp:
        scratch = Path(temp)
        for index, project in enumerate(registry["projects"]):
            source = root / project["path"]
            stage = scratch / str(index)
            stage.mkdir()
            # Exact staging avoids unrelated files nested inside the protocol TS project.
            names = [
                "README.md",
                "LICENSE",
                "pyproject.toml" if project["language"] == "python" else "package.json",
            ]
            names += [
                p.relative_to(source).as_posix()
                for p in (source / "src").rglob("*")
                if p.is_file()
                and "__pycache__" not in p.parts
                and not any(part.endswith(".egg-info") for part in p.parts)
                and p.suffix != ".pyc"
            ]
            if project["distribution"] == "@ptcg/native-shell":
                from scripts.public.policy import source_files

                prefix = project["path"] + "/"
                names += [n.removeprefix(prefix) for n in source_files(root)
                          if n.startswith(prefix + "src-tauri/") or n.startswith(prefix + "ui/")]
            for name in names:
                data = safe_file(root, project["path"] + "/" + name).read_bytes()
                issues = inspect(project["path"] + "/" + name, data)
                if issues:
                    raise ValueError("Build input blocked [" + ",".join(issues) + "]")
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if project["language"] == "python":
                version = tomllib.loads((stage / "pyproject.toml").read_text())[
                    "project"
                ]["version"]
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "build",
                        "--no-isolation",
                        "--wheel",
                        "--sdist",
                        "--outdir",
                        str(output),
                    ],
                    cwd=stage,
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
                wheel = next(
                    output.glob(project["distribution"].replace("-", "_") + "-*.whl")
                )
                with zipfile.ZipFile(wheel) as archive:
                    names_in = archive.namelist()
                    distinfo = (
                        project["distribution"].replace("-", "_")
                        + f"-{version}.dist-info/"
                    )
                    allowed = {
                        n.removeprefix("src/") for n in names if n.startswith("src/")
                    }
                    allowed.update(
                        distinfo + n
                        for n in [
                            "METADATA",
                            "WHEEL",
                            "RECORD",
                            "top_level.txt",
                            "licenses/LICENSE",
                        ]
                    )
                    check_members(((n, archive.read(n)) for n in names_in), allowed)
                    assert distinfo + "licenses/LICENSE" in names_in
                sdist = next(
                    output.glob(project["distribution"].replace("-", "_") + "-*.tar.gz")
                )
                with tarfile.open(sdist) as archive:
                    prefix = project["distribution"].replace("-", "_") + f"-{version}/"
                    allowed = set(names) | {"PKG-INFO", "setup.cfg"}
                    egg = (
                        "src/"
                        + project["distribution"].replace("-", "_")
                        + ".egg-info/"
                    )
                    allowed.update(
                        egg + n
                        for n in [
                            "PKG-INFO",
                            "SOURCES.txt",
                            "dependency_links.txt",
                            "top_level.txt",
                        ]
                    )
                    if project["allowedRuntimeRequirements"]:
                        allowed.add(egg + "requires.txt")
                    members = []
                    for item in archive.getmembers():
                        if item.isdir():
                            continue
                        if not item.isfile() or not item.name.startswith(prefix):
                            raise ValueError("Unsafe sdist member")
                        members.append(
                            (item.name[len(prefix) :], archive.extractfile(item).read())
                        )
                    # egg-info is normal source distribution metadata, not application runtime data.
                    for name, data in members:
                        if name not in allowed or not safe_name(name):
                            raise ValueError("Unexpected sdist member")
                        if any(
                            i != "private_or_generated_path"
                            for i in inspect(name, data)
                        ):
                            raise ValueError("Unsafe sdist content")
                results.append(
                    {"project": project["path"], "artifacts": [wheel.name, sdist.name]}
                )
            else:
                raw = subprocess.check_output(
                    [
                        "npm",
                        "pack",
                        "--ignore-scripts",
                        "--offline",
                        "--cache",
                        str(scratch / "npm-cache"),
                        "--json",
                        "--pack-destination",
                        str(output),
                    ],
                    cwd=stage,
                )
                filename = json.loads(raw)[0]["filename"]
                with tarfile.open(output / filename) as archive:
                    members = []
                    for item in archive.getmembers():
                        if not item.isfile() or not item.name.startswith("package/"):
                            raise ValueError("Unsafe npm archive entry")
                        members.append(
                            (
                                item.name.removeprefix("package/"),
                                archive.extractfile(item).read(),
                            )
                        )
                    check_members(members, set(names))
                results.append({"project": project["path"], "artifacts": [filename]})
        # Install wheels, not editable paths, into a separate interpreter; import outside checkout.
        env = scratch / "installed"
        subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
        python = env / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        wheels = sorted(output.glob("*.whl"))
        # Installation is offline and constrained to the separately pinned closure.
        # Do not silently inherit dependencies from the developer environment.
        if wheelhouse is None:
            raise ValueError("P2 protocol requires --dependency-wheelhouse")
        wheelhouse = Path(wheelhouse).resolve()
        if not wheelhouse.is_dir():
            raise ValueError("Dependency wheelhouse does not exist")
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "--constraint",
                str(root / "packages/battle-protocol/requirements.lock"),
                "--constraint",
                str(root / "packages/battle-core/requirements.lock"),
                "--constraint",
                str(root / "apps/online-server/requirements.lock"),
                "--constraint",
                str(root / "packages/online-client/requirements.lock"),
                "--constraint",
                str(root / "apps/desktop-api/requirements.lock"),
                *[str(p) for p in wheels],
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        modules = [
            p["module"] for p in registry["projects"] if p["language"] == "python"
        ]
        code = (
            "import importlib; modules="
            + repr(modules)
            + '; assert all(importlib.import_module(n).IMPLEMENTATION_READY is False for n in modules); from importlib.resources import files; assert files("ptcg_protocol").joinpath("schema/v2.schema.json").is_file()'
        )
        subprocess.run([str(python), "-I", "-c", code], cwd=scratch, check=True)
        fixture = scratch / "protocol-cases.json"
        fixture.write_bytes((root / "tests/synthetic/protocol/v2.json").read_bytes())
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "import json, sys; from ptcg_protocol import v2; from ptcg_protocol.validation import validate; "
                'cases=json.load(open("protocol-cases.json")); '
                'assert all(validate(c["kind"], c["value"]) == c["value"] for c in cases); '
                'assert not any(n.split(".")[0] in {"apps", "packages", "ptcg", "fastapi", "torch", "sqlite3"} for n in sys.modules)',
            ],
            cwd=scratch,
            check=True,
        )
        provider_smoke = scratch / "provider-smoke.py"
        provider_smoke.write_bytes((root / "tests/providers/synthetic.py").read_bytes())
        subprocess.run(
            [str(python), "-I", str(provider_smoke)], cwd=scratch, check=True
        )
        core_smoke = scratch / "core-smoke.py"
        core_smoke.write_bytes((root / "tests/core/smoke.py").read_bytes())
        subprocess.run([str(python), "-I", str(core_smoke)], cwd=scratch, check=True)
        online_smoke = scratch / "online-smoke.py"
        online_smoke.write_bytes((root / "tests/online_v2/smoke.py").read_bytes())
        subprocess.run([str(python), "-I", str(online_smoke)], cwd=scratch, check=True)
        client_smoke = scratch / "client-smoke.py"
        client_smoke.write_bytes((root / "tests/online_client/smoke.py").read_bytes())
        subprocess.run([str(python), "-I", str(client_smoke)], cwd=scratch, check=True)
        bridge_smoke = scratch / "bridge-smoke.py"
        bridge_smoke.write_bytes((root / "tests/desktop_bridge/smoke.py").read_bytes())
        subprocess.run([str(python), "-I", str(bridge_smoke)], cwd=scratch, check=True)
    hashes = {
        p.name: hashlib.file_digest(p.open("rb"), "sha256").hexdigest()
        for p in output.iterdir()
        if p.is_file()
    }
    report = {
        "projects": results,
        "wheelInstallOutsideCheckout": "passed",
        "protocolValidationOutsideCheckout": "passed",
        "providerValidationOutsideCheckout": "passed",
        "coreLoaderOutsideCheckout": "passed",
        "onlineAPIOutsideCheckout": "passed",
        "onlineClientOutsideCheckout": "passed",
        "desktopBridgeOutsideCheckout": "passed",
        "files": hashes,
        "productionReady": False,
    }
    (output / "BUILD-REPORT.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="New output directory"
    )
    parser.add_argument("--dependency-wheelhouse", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.output, wheelhouse=args.dependency_wheelhouse)
    print(
        json.dumps(
            {
                "projects": len(report["projects"]),
                "artifacts": len(report["files"]),
                "productionReady": False,
            }
        )
    )
