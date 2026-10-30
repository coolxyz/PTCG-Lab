"""Explicit runtime closure trimming, applied only to a verified new payload.

Validation scripts and referenced first-party test source are provenance inputs;
pytest is not a product runtime dependency. httpx remains because the current
release worker uses Starlette TestClient as its local in-process transport.
"""

from email.parser import BytesParser
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil

from packaging.requirements import Requirement
from packaging.markers import default_environment
from scripts.packaging.desktop_inventory import payload_files
from scripts.public.policy import safe_file

REMOVE = {
    "pip",
    "setuptools",
    "setuptools-scm",
    "wheel",
    "packaging",
    "pytest",
    "pluggy",
    "iniconfig",
    "ruff",
}
MODULES = {
    "pip",
    "setuptools",
    "pkg_resources",
    "_distutils_hack",
    "setuptools_scm",
    "wheel",
    "packaging",
    "pytest",
    "_pytest",
    "pluggy",
    "iniconfig",
    "ruff",
}


def normalized(name):
    return re.sub("[-_.]+", "-", name).lower()


def trim(payload, *, root):
    root, payload = Path(root), Path(payload)
    manifest = json.loads((payload / "manifest.json").read_text(encoding="utf-8"))
    platform = manifest["platform"]
    payload_files(payload, platform)
    old_bytes = sum(p.stat().st_size for p in payload.rglob("*") if p.is_file())
    removed = []
    removed_distributions = []
    provenance = []
    app = payload / "app"
    (payload / "INSTALLATION.json").write_text(
        safe_file(root, "scripts/native/installation-contract.json").read_text(
            encoding="utf-8"
        ), encoding="utf-8"
    )
    for name in (
        "scripts/sync/verify_runtime.py",
        "scripts/product/resources.py",
        "scripts/product/display_resources.py",
        "scripts/product/desktop_install.py",
    ):
        source = safe_file(root, name)
        target = app / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    path = app / "data/sync/implementations.json"
    if path.exists():
        for value in (
            json.loads(path.read_text(encoding="utf-8")).get("cards", {}).values()
        ):
            for name in value.get("testFiles", []):
                if not name.startswith("tests/") or not name.endswith(".py"):
                    raise ValueError("Invalid first-party verification provenance")
                source = safe_file(root, name)
                target = app / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                provenance.append(
                    {"path": name, "sha256": sha256(source.read_bytes()).hexdigest()}
                )
    site = payload / (
        "python/lib/python3.14/site-packages"
        if platform == "macos-arm64"
        else "python/Lib/site-packages"
    )

    def remove(path):
        if path.exists():
            removed.append(path.relative_to(payload).as_posix())
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    for dist in sorted(site.glob("*.dist-info")):
        meta = BytesParser().parsebytes((dist / "METADATA").read_bytes())
        if normalized(meta["Name"]) in REMOVE:
            removed_distributions.append(normalized(meta["Name"]))
            remove(dist)
    for name in MODULES:
        remove(site / name)
        remove(site / (name + ".py"))
    remove(site / "distutils-precedence.pth")
    stdlib = payload / (
        "python/lib/python3.14" if platform == "macos-arm64" else "python/Lib"
    )
    for name in ("idlelib", "tkinter", "turtledemo", "turtle.py", "test", "ensurepip"):
        remove(stdlib / name)
    if platform == "macos-arm64":
        for base, patterns in [
            (
                payload / "python/bin",
                (
                    "pip*",
                    "pytest*",
                    "py.test",
                    "ruff",
                    "wheel",
                    "idle*",
                    "python*-config",
                    "tclsh*",
                    "wish*",
                ),
            ),
            (
                payload / "python/lib",
                (
                    "libpython*.a",
                    "libtcl*",
                    "libtk*",
                    "tcl*",
                    "tk*",
                    "itcl*",
                    "pkgconfig",
                ),
            ),
            (stdlib / "lib-dynload", ("_tkinter*",)),
        ]:
            for pattern in patterns:
                for p in base.glob(pattern):
                    remove(p)
        remove(payload / "python/include")
    else:
        for pattern in ("_tkinter*", "tcl*", "tk*"):
            for p in (payload / "python/DLLs").glob(pattern):
                remove(p)
        remove(payload / "python/tcl")
        remove(payload / "python/include")
        remove(payload / "python/libs")
    installed = {}
    for dist in site.glob("*.dist-info"):
        metadata = BytesParser().parsebytes((dist / "METADATA").read_bytes())
        installed[normalized(metadata["Name"])] = metadata
    environment = {
        **default_environment(),
        "python_version": "3.14",
        "python_full_version": "3.14.10",
        "sys_platform": "darwin" if platform == "macos-arm64" else "win32",
        "platform_system": "Darwin" if platform == "macos-arm64" else "Windows",
        "os_name": "posix" if platform == "macos-arm64" else "nt",
        "platform_machine": "arm64" if platform == "macos-arm64" else "AMD64",
        "extra": "",
    }
    for meta in installed.values():
        for value in meta.get_all("Requires-Dist", []):
            requirement = Requirement(value)
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            target = installed.get(normalized(requirement.name))
            if target is None or target["Version"] not in requirement.specifier:
                raise ValueError(
                    "Runtime dependency closure incomplete: " + requirement.name
                )
    for p in payload.rglob("__pycache__"):
        remove(p)
    for p in site.glob("*.dist-info/direct_url.json"):
        remove(p)
    report = dict(
        schema=1,
        stage="P8-C2-runtime-closure",
        platform=platform,
        removed=removed,
        removedDistributions=sorted(removed_distributions),
        prohibitedDistributions=sorted(REMOVE),
        remainingDistributions=sorted(installed),
        externalGitRequired=False,
        formalRuleEvidenceRequired=True,
        verificationProvenance=sorted(
            {p["path"]: p for p in provenance}.values(), key=lambda p: p["path"]
        ),
        beforeBytes=old_bytes,
        afterBytes=sum(p.stat().st_size for p in payload.rglob("*") if p.is_file()),
        releaseReady=False,
    )
    (payload / "RUNTIME-CLOSURE.json").write_text(
        json.dumps(report, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest["files"] = {
        p.relative_to(payload).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in sorted(payload.rglob("*"))
        if p.is_file() and p != payload / "manifest.json"
    }
    (payload / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    payload_files(payload, platform)
    return report
