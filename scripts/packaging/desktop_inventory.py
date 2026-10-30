"""Offline component and license inventory from the actual sealed payload.

Records metadata, not a license approval. Cargo.lock is an intentionally broad
build/other-target closure; runtime composite subcomponents remain reviewable.
No target executable, network, user profile or package installer is invoked.
"""

from datetime import datetime, timezone
from email.parser import BytesParser
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tomllib
from urllib.parse import quote
from packaging.licenses import canonicalize_license_expression, InvalidLicenseExpression

from scripts.packaging.common import record, new_output
from scripts.public.policy import safe_file


def payload_files(payload, platform):
    payload = Path(payload).absolute()
    if any(
        p.is_symlink() or p.is_junction()
        for p in (payload, *payload.parents, *payload.rglob("*"))
    ):
        raise ValueError("Payload links forbidden")
    manifest = json.loads(
        safe_file(payload, "manifest.json").read_text(encoding="utf-8")
    )
    if (
        manifest.get("schema") != 1
        or manifest.get("platform") != platform
        or not isinstance(manifest.get("files"), dict)
        or not 1 <= len(manifest["files"]) <= 20000
    ):
        raise ValueError("Invalid payload manifest")
    actual = {
        p.relative_to(payload).as_posix() for p in payload.rglob("*") if p.is_file()
    }
    if actual != set(manifest["files"]) | {"manifest.json"}:
        raise ValueError("Unexpected payload file")
    for name, digest in manifest["files"].items():
        path = safe_file(payload, name)
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Changed payload file")
        if (
            {
                "credentials",
                "launch",
                "__pycache__",
                ".git",
                "node_modules",
            }.intersection(PurePosixPath(name).parts)
            or path.name
            in {
                "connection.json",
                "native.lock",
                "shell.lock",
                "product.sqlite",
                "vault.json",
                ".env",
            }
            or path.suffix == ".pyc"
        ):
            raise ValueError("User state or cache in payload")
    return manifest["files"]


def relative(name):
    p = PurePosixPath(name)
    return (
        bool(name)
        and not p.is_absolute()
        and p.as_posix() == name
        and not any(
            part in {"", "..", "."} or ":" in part or "\\" in part for part in p.parts
        )
    )


def generate(payload, output, *, root, cargo_home):
    root, payload = Path(root), Path(payload)
    platform = json.loads((payload / "manifest.json").read_text(encoding="utf-8"))[
        "platform"
    ]
    files = payload_files(payload, platform)
    if platform not in {"macos-arm64", "windows-x64"}:
        raise ValueError("Unprepared target")
    output = new_output(output)
    components = []
    licenses = []

    def add(kind, name, version, scope, declared=None, expression=None, source=None):
        if (
            not name
            or not version
            or any(c in str(name) + str(version) for c in "\r\n\x00")
        ):
            raise ValueError("Invalid component identity")
        key = sha256((kind + "\0" + name + "\0" + version).encode()).hexdigest()[:24]
        value = dict(
            id="SPDXRef-" + key,
            kind=kind,
            name=name,
            version=version,
            scope=scope,
            licenseDeclared=expression or "NOASSERTION",
            declaredLicenseMetadata=declared,
            source=source,
            licenseFiles=[],
        )
        if any(c["id"] == value["id"] for c in components):
            raise ValueError("Duplicate component")
        components.append(value)
        return value

    def copy_license(component, base, path):
        path = Path(path)
        base = Path(base)
        name = path.relative_to(base).as_posix()
        if not relative(name):
            raise ValueError("Unsafe license path")
        raw = safe_file(base, name).read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Oversized license evidence")
        target = "licenses/" + component["id"] + "/" + name
        destination = output / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        component["licenseFiles"].append(target)
        licenses.append(
            dict(path=target, sha256=sha256(raw).hexdigest(), component=component["id"])
        )

    lock_name = (
        "runtime-lock.json"
        if platform.startswith("macos")
        else "runtime-windows-lock.json"
    )
    runtime = json.loads(
        (root / "scripts/native" / lock_name).read_text(encoding="utf-8")
    )
    item = add(
        "runtime",
        "CPython standalone composite",
        runtime["python"],
        "shipped-runtime-composite",
        source={"url": runtime["url"], "sha256": runtime["sha256"]},
    )
    runtime_license = (
        "python/lib/python3.14/LICENSE.txt"
        if platform.startswith("macos")
        else "python/LICENSE.txt"
    )
    if runtime_license in files:
        copy_license(item, payload, payload / runtime_license)
    sites = (
        list((payload / "python/lib").glob("python*/site-packages"))
        if platform.startswith("macos")
        else [payload / "python/Lib/site-packages"]
    )
    if len(sites) != 1 or not sites[0].is_dir():
        raise ValueError("Unique installed site-packages required")
    for dist in sorted(sites[0].glob("*.dist-info")):
        metadata = BytesParser().parsebytes(safe_file(dist, "METADATA").read_bytes())
        item = add(
            "pypi",
            metadata.get("Name"),
            metadata.get("Version"),
            "shipped-python",
            metadata.get("License"),
            metadata.get("License-Expression"),
        )
        declared = metadata.get_all("License-File", [])
        for name in declared:
            if not relative(name):
                raise ValueError("Unsafe metadata license path")
        selected = {
            p
            for p in dist.rglob("*")
            if p.is_file()
            and (
                p.name.lower().startswith(("license", "copying", "notice", "authors"))
                or "licenses" in p.relative_to(dist).parts
            )
        }
        for name in declared:
            found = [dist / name, dist / "licenses" / name]
            if not any(p.is_file() for p in found):
                item.setdefault("missingLicenseFiles", []).append(name)
            selected.update(p for p in found if p.is_file())
        for path in sorted(selected):
            copy_license(item, dist, path)
    # Inventory actual installed frontend runtime dependencies, recursively. Dev
    # dependencies and Tauri's JS build CLI are excluded from this runtime group.
    npm_lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))[
        "packages"
    ]
    seen = set()

    def npm(name):
        if name in seen:
            return
        seen.add(name)
        if not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", name):
            raise ValueError("Invalid npm identity")
        locked = npm_lock.get("node_modules/" + name)
        if not isinstance(locked, dict):
            raise ValueError("Frontend dependency missing from root lock")
        if locked.get("link"):
            resolved = locked.get("resolved", "")
            if not relative(resolved):
                raise ValueError("Unsafe workspace lock path")
            base = root / resolved
            locked = npm_lock.get(resolved, {})
        else:
            base = (root / "node_modules" / name).resolve()
        if not base.is_dir():
            raise ValueError("Build frontend dependencies first")
        meta = json.loads(safe_file(base, "package.json").read_text(encoding="utf-8"))
        if meta["name"] != name or meta["version"] != locked.get("version"):
            raise ValueError("Wrong installed npm identity")
        declared = meta.get("license")
        item = add(
            "npm",
            name,
            meta["version"],
            "frontend-runtime",
            declared,
            declared
            if isinstance(declared, str)
            and re.fullmatch(r"[A-Za-z0-9.()+ -]+", declared)
            else None,
        )
        for path in sorted(base.iterdir()):
            if path.is_file() and path.name.lower().startswith(
                ("license", "copying", "notice")
            ):
                copy_license(item, base, path)
        for dependency in sorted(meta.get("dependencies", {})):
            npm(dependency)

    for project in ("apps/web", "apps/desktop-web"):
        meta = json.loads((root / project / "package.json").read_text(encoding="utf-8"))
        for name in sorted(meta.get("dependencies", {})):
            npm(name)
    cargo = tomllib.loads(
        (root / "apps/native-shell/src-tauri/Cargo.lock").read_text(encoding="utf-8")
    )
    registry = Path(cargo_home) / "registry/src"
    for package in sorted(
        cargo["package"], key=lambda v: (v["name"], v["version"], v.get("source", ""))
    ):
        if "source" not in package:
            continue
        item = add(
            "cargo",
            package["name"],
            package["version"],
            "locked-build-and-other-target-closure",
            source={"registry": package["source"], "sha256": package.get("checksum")},
        )
        matches = list(registry.glob("*/" + package["name"] + "-" + package["version"]))
        if len(matches) != 1:
            item["metadataAvailable"] = False
            continue
        base = matches[0]
        meta = tomllib.loads(safe_file(base, "Cargo.toml").read_text(encoding="utf-8"))[
            "package"
        ]
        item["declaredLicenseMetadata"] = meta.get("license")
        item["licenseDeclared"] = meta.get("license") or "NOASSERTION"
        selected = {
            p
            for p in base.iterdir()
            if p.is_file()
            and p.name.lower().startswith(("license", "copying", "notice"))
        }
        if name := meta.get("license-file"):
            if not relative(name):
                raise ValueError("Unsafe crate license path")
            if (base / name).is_file():
                selected.add(base / name)
        for path in sorted(selected):
            copy_license(item, base, path)
        item["metadataAvailable"] = True
    for name, path in [
        ("PTCG Lab", "LICENSE"),
        ("ptcg-engine", "runtime/engine/UPSTREAM-LICENSE"),
    ]:
        base = root if name == "PTCG Lab" else payload / "app"
        item = add(
            "source",
            name,
            "current",
            "shipped-source",
            source={"sha256": sha256(safe_file(base, path).read_bytes()).hexdigest()},
        )
        copy_license(item, base, base / path)
    resource_files = {k: v for k, v in files.items() if k.startswith("app/data/")}
    identity = sha256(json.dumps(resource_files, sort_keys=True).encode()).hexdigest()
    add(
        "assets",
        "approved private card assets",
        identity,
        "private-assets-review-required",
    )
    inventory = dict(
        schema=1,
        platform=platform,
        payloadManifestSha256=sha256(
            (payload / "manifest.json").read_bytes()
        ).hexdigest(),
        components=components,
        licenseFiles=licenses,
        missingLicenseComponents=[c["id"] for c in components if not c["licenseFiles"]],
        runtimeCompositeReview="pending",
        licenseReview="pending",
        redistributionApproved=False,
        releaseReady=False,
    )
    spdx_packages = []
    for component in components:
        try:
            component["licenseDeclared"] = str(
                canonicalize_license_expression(component["licenseDeclared"])
            )
        except InvalidLicenseExpression:
            component["licenseDeclared"] = "NOASSERTION"
    record(output / "COMPONENTS.json", inventory)
    for c in components:
        package = dict(
            SPDXID=c["id"],
            name=c["name"],
            versionInfo=c["version"],
            downloadLocation="NOASSERTION",
            filesAnalyzed=False,
            licenseConcluded="NOASSERTION",
            licenseDeclared=c["licenseDeclared"],
            copyrightText="NOASSERTION",
            comment="Scope: "
            + c["scope"]
            + "; license evidence: "
            + ", ".join(c["licenseFiles"]),
        )
        if c["kind"] in {"npm", "pypi", "cargo"}:
            package["externalRefs"] = [
                dict(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceType="purl",
                    referenceLocator="pkg:"
                    + c["kind"]
                    + "/"
                    + quote(c["name"], safe="/")
                    + "@"
                    + quote(c["version"], safe=""),
                )
            ]
        spdx_packages.append(package)
    namespace = sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    document = dict(
        spdxVersion="SPDX-2.3",
        dataLicense="CC0-1.0",
        SPDXID="SPDXRef-DOCUMENT",
        name="PTCG Lab " + platform + " component inventory",
        documentNamespace="https://spdx.org/spdxdocs/ptcg-" + namespace,
        creationInfo=dict(
            creators=["Tool: PTCG-Lab-desktop-inventory"],
            created=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        packages=spdx_packages,
        relationships=[
            dict(
                spdxElementId="SPDXRef-DOCUMENT",
                relationshipType="DESCRIBES",
                relatedSpdxElement=c["id"],
            )
            for c in components
        ],
    )
    record(output / "SBOM.spdx.json", document)
    shutil.copy2(root / "THIRD_PARTY_NOTICES.md", output / "THIRD_PARTY_NOTICES.md")
    return inventory
