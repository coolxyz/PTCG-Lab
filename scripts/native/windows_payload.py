"""Prepare a Windows x64 backend without executing any target binary on the host.

Only locked wheels are installed, offline; selected card assets are explicit input.
Native execution and WebView acceptance are independent, mandatory later checks.
"""

import argparse
from email.parser import BytesParser
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.native.assemble import LEGACY, copy_file  # noqa: E402
from scripts.native.layout import DATA, PROJECTS, install_artwork_index  # noqa: E402
from scripts.public.policy import safe_file, source_files  # noqa: E402


def canonical(name):
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and path.as_posix() == name
        and ".." not in path.parts
        and not any("\\" in p or ":" in p or p.rstrip(". ") != p for p in path.parts)
    )


def wheel_info(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(
            not canonical(n.rstrip("/")) for n in names
        ):
            raise ValueError("Unsafe wheel paths")
        metadata = [n for n in names if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1 or any(
            (i.external_attr >> 16) & 0o170000 == 0o120000 for i in archive.infolist()
        ):
            raise ValueError("Unsafe wheel metadata or link")
        return BytesParser().parsebytes(archive.read(metadata[0]))


def verified_wheels(directory, lock, *, root=ROOT):
    from packaging.requirements import Requirement
    from packaging.tags import cpython_tags, compatible_tags
    from packaging.utils import canonicalize_name, parse_wheel_filename
    from packaging.specifiers import SpecifierSet

    if (
        lock.get("schema") != 1
        or lock.get("platform") != "win_amd64"
        or lock.get("python") != "3.14.10"
        or lock.get("abi") != "cp314"
    ):
        raise ValueError("Unsupported Windows wheel lock")

    selected = []
    packages = {}
    tags = set(cpython_tags((3, 14), ["cp314", "abi3"], ["win_amd64"])) | set(
        compatible_tags((3, 14), "cp314", ["win_amd64"])
    )
    for item in lock["wheels"]:
        name = item["filename"]
        if not canonical(name) or "/" in name:
            raise ValueError("Unsafe locked filename")
        path = safe_file(directory, name)
        if sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Wheel hash mismatch")
        _, _, _, wheel_tags = parse_wheel_filename(name)
        if not tags.intersection(wheel_tags):
            raise ValueError("Wrong Windows wheel ABI")
        info = wheel_info(path)
        if info.get("Requires-Python") and "3.14.10" not in SpecifierSet(
            info["Requires-Python"]
        ):
            raise ValueError("Wheel requires a different Python version")
        key = canonicalize_name(info["Name"])
        if (
            key in packages
            or key != canonicalize_name(item["name"])
            or info["Version"] != item["version"]
        ):
            raise ValueError("Wheel identity mismatch")
        packages[key] = info
        selected.append(path)
    expected = {p.name for p in selected}
    if {p.name for p in Path(directory).iterdir()} != expected:
        raise ValueError("Unexpected wheelhouse entries")
    environment = {
        "os_name": "nt",
        "sys_platform": "win32",
        "platform_system": "Windows",
        "platform_machine": "AMD64",
        "python_version": "3.14",
        "python_full_version": "3.14.10",
        "implementation_name": "cpython",
        "implementation_version": "3.14.10",
        "platform_python_implementation": "CPython",
        "platform_release": "10",
        "platform_version": "10",
        "extra": "",
    }
    for info in packages.values():
        for value in info.get_all("Requires-Dist", []):
            requirement = Requirement(value)
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            dependency = packages.get(canonicalize_name(requirement.name))
            if dependency is None or dependency["Version"] not in requirement.specifier:
                raise ValueError(
                    "Windows dependency closure incomplete: " + requirement.name
                )
    return selected


def internal_wheels(directory, root=ROOT):
    selected = []
    for project in PROJECTS:
        info = tomllib.loads((Path(root) / project / "pyproject.toml").read_text())[
            "project"
        ]
        name = (
            info["name"].replace("-", "_") + "-" + info["version"] + "-py3-none-any.whl"
        )
        path = safe_file(directory, name)
        meta = wheel_info(path)
        if meta["Name"] != info["name"] or meta["Version"] != info["version"]:
            raise ValueError("Internal wheel/source version mismatch")
        selected.append(path)
    return selected


def extract_runtime(archive, target, lock):
    if sha256(Path(archive).read_bytes()).hexdigest() != lock["sha256"]:
        raise ValueError("Windows runtime hash mismatch")
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        seen = set()
        for member in members:
            name = member.name.rstrip("/")
            if (
                not canonical(name)
                or name in seen
                or (name != "python" and not name.startswith("python/"))
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError("Unsafe Windows runtime archive")
            seen.add(name)
        tar.extractall(target, filter="data")
    python = Path(target) / "python"
    # The shipped runtime has no installer, build tools or global postinstall.
    shutil.rmtree(python / "Lib/site-packages")
    (python / "Lib/site-packages").mkdir()
    shutil.rmtree(python / "Scripts", ignore_errors=True)
    for name in ["python.exe", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"]:
        pe_machine(python / name)


def pe_machine(path):
    raw = Path(path).read_bytes()
    if len(raw) < 64 or raw[:2] != b"MZ":
        raise ValueError("Invalid Windows PE file")
    offset = struct.unpack_from("<I", raw, 60)[0]
    if (
        offset + 24 > len(raw)
        or raw[offset : offset + 4] != b"PE\x00\x00"
        or struct.unpack_from("<H", raw, offset + 4)[0] != 0x8664
    ):
        raise ValueError("Expected Windows x64 PE")
    return "AMD64"


def assemble(runtime, wheelhouse, internal, assets, output, *, root=ROOT):
    root, assets = Path(root).resolve(), Path(assets).resolve()
    output = Path(output).absolute()
    if output.exists() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("Choose a new local payload directory")
    lock = json.loads((root / "scripts/native/runtime-windows-lock.json").read_text())
    if (
        lock.get("schema") != 1
        or lock.get("platform") != "windows-x64"
        or lock.get("python") != "3.14.10"
        or lock.get("abi") != "cp314"
        or lock.get("target") != "x86_64-pc-windows-msvc"
    ):
        raise ValueError("Unsupported Windows runtime lock")
    wheels_lock = json.loads(
        (root / "scripts/native/windows-wheel-lock.json").read_text()
    )
    selected = verified_wheels(wheelhouse, wheels_lock, root=root) + internal_wheels(
        internal, root
    )
    # Verify every explicit data input before creating a partially populated tree.
    for name in DATA:
        safe_file(assets, "data/" + name)
    for folder, entry in (
        ("apps/web/dist", "index.html"),
        ("apps/desktop-web/dist", "online.html"),
    ):
        safe_file(root, folder + "/" + entry)
    output.mkdir(parents=True)
    extract_runtime(runtime, output, lock)
    site = output / "python/Lib/site-packages"
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--no-index",
            "--no-deps",
            "--no-compile",
            "--only-binary=:all:",
            "--platform",
            "win_amd64",
            "--python-version",
            "3.14",
            "--implementation",
            "cp",
            "--abi",
            "cp314",
            "--abi",
            "abi3",
            "--target",
            str(site),
            *map(str, selected),
        ],
        check=True,
    )
    shutil.rmtree(site / "bin", ignore_errors=True)
    shutil.rmtree(site / "Scripts", ignore_errors=True)
    for p in site.glob("*.dist-info/direct_url.json"):
        p.unlink()
    app = output / "app"
    for name in source_files(root):
        if name in {
            "packages/__init__.py",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "scripts/engine/build_engine.py",
        } or name.startswith(("apps/api/", "rulesets/", *LEGACY)):
            copy_file(safe_file(root, name), app / name)
    for name in DATA:
        copy_file(safe_file(assets, "data/" + name), app / "data" / name)
    install_artwork_index(root, assets, app)
    for p in (assets / "data/sync").glob("*.json"):
        name = p.relative_to(assets).as_posix()
        copy_file(safe_file(assets, name), app / name)
    for p in (assets / "runtime/engine").rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            name = p.relative_to(assets).as_posix()
            copy_file(safe_file(assets, name), app / name)
    copy_file(
        safe_file(assets, "artifacts/engine/overlay-hashes.json"),
        app / "artifacts/engine/overlay-hashes.json",
    )
    for folder in ("apps/web/dist", "apps/desktop-web/dist"):
        for p in (root / folder).rglob("*"):
            if p.is_file():
                copy_file(
                    safe_file(root, p.relative_to(root).as_posix()),
                    app / p.relative_to(root),
                )
    (output / "bootstrap.py").write_text(
        'import pathlib,runpy,sys\nsys.path.insert(0,str(pathlib.Path(__file__).resolve().parent/"app"))\nrunpy.run_module("apps.api.native_product",run_name="__main__")\n',
        encoding="utf-8",
        newline="\n",
    )
    for p in output.rglob("__pycache__"):
        shutil.rmtree(p)
    from scripts.packaging.source_identity import source_digest

    (output / "BUILD-SOURCE.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "nativeSourceSha256": source_digest(root),
                "runtimeSha256": lock["sha256"],
                "platform": "windows-x64",
                "python": lock["python"],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    binaries = 0
    for p in output.rglob("*"):
        if p.suffix.lower() in {".dll", ".exe", ".pyd"}:
            pe_machine(p)
            binaries += 1
    files = {
        p.relative_to(output).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    if len(files) > 20000:
        raise ValueError("Payload exceeds native manifest limit")
    manifest = {
        "schema": 1,
        "platform": "windows-x64",
        "python": "python/python.exe",
        "files": files,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    from scripts.packaging.runtime_trim import trim

    trim(output, root=root)
    files = json.loads((output / "manifest.json").read_text(encoding="utf-8"))["files"]
    binaries = sum(
        pe_machine(output / name) == "AMD64"
        for name in files
        if Path(name).suffix.lower() in {".exe", ".dll", ".pyd"}
    )
    return {
        "schema": 1,
        "stage": "P8-B-payload",
        "status": "cross-prepared",
        "python": lock["python"],
        "externalWheels": len(wheels_lock["wheels"]),
        "internalWheels": len(PROJECTS),
        "peFilesAMD64": binaries,
        "files": len(files),
        "runtimeSha256": lock["sha256"],
        "wheelLockSha256": sha256(
            (root / "scripts/native/windows-wheel-lock.json").read_bytes()
        ).hexdigest(),
        "nativeExecution": "pending",
        "guiAccepted": False,
        "releaseReady": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime", "wheelhouse", "internal", "assets", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            assemble(
                args.runtime, args.wheelhouse, args.internal, args.assets, args.output
            )
        )
    )
