"""Read-only macOS APP architecture, loader, payload and signature audit.

System dyld libraries are permitted; developer-machine absolute dependencies,
escaping rpaths and the wrong target/minimum OS fail closed. No code is signed,
installed, repaired or executed by this module.
"""

from hashlib import sha256
import json
from pathlib import Path
import plistlib
import struct
import posixpath
import subprocess

from scripts.packaging.desktop_inventory import payload_files
from scripts.public.policy import safe_file

UNSUPPORTED = {b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce"}
MAGIC = {b"\xcf\xfa\xed\xfe": "<", b"\xfe\xed\xfa\xcf": ">"}
ARCH = {0x100000C: "arm64", 0x1000007: "x86_64"}
DYLIBS = {0xC, 0x80000018, 0x8000001F, 0x20, 0x80000023}


def version(value):
    return ".".join(str(n) for n in (value >> 16, (value >> 8) & 255, value & 255))


def macho(raw):
    """Parse bounded 64-bit load commands; fat slices retain their own limits."""
    if raw[:4] in UNSUPPORTED:
        raise ValueError("Unsupported 32-bit Mach-O")
    if raw[:4] in {
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }:
        endian = ">" if raw[:4] in {b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"} else "<"
        fat64 = raw[:4] in {b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
        if len(raw) < 8:
            raise ValueError("Truncated fat header")
        count = struct.unpack_from(endian + "I", raw, 4)[0]
        size = 32 if fat64 else 20
        if not 1 <= count <= 16 or 8 + size * count > len(raw):
            raise ValueError("Invalid fat slices")
        result = []
        ranges = []
        for i in range(count):
            start = 8 + i * size
            cpu = struct.unpack_from(endian + "I", raw, start)[0]
            offset, length = struct.unpack_from(
                endian + ("QQ" if fat64 else "II"), raw, start + 8
            )
            if (
                offset < 8 + size * count
                or length < 32
                or offset + length > len(raw)
                or any(offset < a + b and a < offset + length for a, b in ranges)
            ):
                raise ValueError("Invalid fat slice extent")
            ranges.append((offset, length))
            parts = macho(raw[offset : offset + length])
            if len(parts) != 1 or parts[0]["architecture"] != ARCH.get(cpu):
                raise ValueError("Fat architecture mismatch")
            result.extend(parts)
        if len({p["architecture"] for p in result}) != len(result):
            raise ValueError("Duplicate architecture")
        return result
    if raw[:4] not in MAGIC:
        return []
    endian = MAGIC[raw[:4]]
    if len(raw) < 32:
        raise ValueError("Truncated Mach-O header")
    _, cpu, _, _, count, total, _, _ = struct.unpack_from(endian + "8I", raw)
    if cpu not in ARCH or count > 65536 or total > len(raw) - 32:
        raise ValueError("Invalid Mach-O header")
    cursor = 32
    end = cursor + total
    dependencies = []
    rpaths = []
    minimum = None
    for _ in range(count):
        if cursor + 8 > end:
            raise ValueError("Truncated load command")
        kind, size = struct.unpack_from(endian + "II", raw, cursor)
        if size < 8 or size % 4 or cursor + size > end:
            raise ValueError("Invalid load command size")
        if kind in DYLIBS | {0x8000001C}:
            if size < (24 if kind in DYLIBS else 12):
                raise ValueError("Truncated loader command")
            offset = struct.unpack_from(endian + "I", raw, cursor + 8)[0]
            if offset < (24 if kind in DYLIBS else 12) or offset >= size:
                raise ValueError("Invalid loader string offset")
            value = raw[cursor + offset : cursor + size]
            if b"\0" not in value:
                raise ValueError("Unterminated loader path")
            value = value.split(b"\0", 1)[0].decode("utf-8")
            (dependencies if kind in DYLIBS else rpaths).append(value)
        elif kind in {0x32, 0x24}:
            if size < (24 if kind == 0x32 else 16) or minimum is not None:
                raise ValueError("Invalid minimum OS command")
            if (
                kind == 0x32
                and struct.unpack_from(endian + "I", raw, cursor + 8)[0] != 1
            ):
                raise ValueError("Non-macOS binary")
            minimum = version(
                struct.unpack_from(
                    endian + "I", raw, cursor + (12 if kind == 0x32 else 8)
                )[0]
            )
        cursor += size
    if cursor != end or minimum is None:
        raise ValueError("Incomplete Mach-O commands")
    return [
        dict(
            architecture=ARCH[cpu],
            minimumMacOS=minimum,
            dependencies=dependencies,
            rpaths=rpaths,
        )
    ]


def inside(base, path):
    path = path.resolve()
    if not path.is_relative_to(base):
        raise ValueError("Loader path escapes APP")
    return path


def expand(value, loader, executable, app):
    for token, base in (("@loader_path", loader), ("@executable_path", executable)):
        if value == token or value.startswith(token + "/"):
            return inside(app, base / value[len(token) :].lstrip("/"))
    raise ValueError("External or unsupported loader path")


def audit(app, *, minimum="12.0", architecture="arm64", root=None):
    app = Path(app).absolute()
    if any(
        p.is_symlink() or p.is_junction() for p in (app, *app.parents, *app.rglob("*"))
    ):
        raise ValueError("APP links forbidden")
    info = plistlib.loads(safe_file(app, "Contents/Info.plist").read_bytes())
    name = info["CFBundleExecutable"]
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("Invalid APP executable")
    executable = app / "Contents/MacOS" / name
    safe_file(app, executable.relative_to(app).as_posix())
    files = payload_files(
        app / "Contents/Resources/backend",
        "macos-arm64" if architecture == "arm64" else "macos-x86_64",
    )
    manifest_sha = sha256(
        (app / "Contents/Resources/backend/manifest.json").read_bytes()
    ).hexdigest()
    build = json.loads(
        safe_file(app, "Contents/Resources/distribution/BUILD-SOURCE.json").read_text(
            encoding="utf-8"
        )
    )
    if build.get("schema") != 1 or build.get("rulePolicy") != "current-only-v1":
        raise ValueError("Missing packaging provenance")
    if root is not None:
        from scripts.packaging.source_identity import source_digest

        if build.get("packagingSourceSha256") != source_digest(root):
            raise ValueError("Packaging sources changed; rebuild candidate")
    inventory = json.loads(
        safe_file(app, "Contents/Resources/distribution/COMPONENTS.json").read_text(
            encoding="utf-8"
        )
    )
    if inventory.get("payloadManifestSha256") != manifest_sha:
        raise ValueError("Inventory/payload mismatch")
    sbom = json.loads(
        safe_file(app, "Contents/Resources/distribution/SBOM.spdx.json").read_text(
            encoding="utf-8"
        )
    )
    if sbom.get("spdxVersion") != "SPDX-2.3" or {
        p["SPDXID"] for p in sbom["packages"]
    } != {c["id"] for c in inventory["components"]}:
        raise ValueError("SBOM component inventory mismatch")
    for entry in inventory["licenseFiles"]:
        path = safe_file(app / "Contents/Resources/distribution", entry["path"])
        if sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("License evidence changed")
    paths = {}
    for path in sorted(app.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                magic = stream.read(4)
            if (
                magic in MAGIC
                or magic in UNSUPPORTED
                or magic
                in {
                    b"\xca\xfe\xba\xbe",
                    b"\xbe\xba\xfe\xca",
                    b"\xca\xfe\xba\xbf",
                    b"\xbf\xba\xfe\xca",
                }
            ):
                paths[path] = macho(path.read_bytes())
    if executable not in paths:
        raise ValueError("Native shell missing")
    exe_slice = next(
        (p for p in paths[executable] if p["architecture"] == architecture), None
    )
    if exe_slice is None:
        raise ValueError("Wrong shell architecture")
    binaries = []
    maximum = (0, 0, 0)
    for path, slices in paths.items():
        selected = next((p for p in slices if p["architecture"] == architecture), None)
        if selected is None:
            raise ValueError("Foreign native architecture in APP")
        min_value = tuple(map(int, selected["minimumMacOS"].split(".")))
        if min_value > tuple(map(int, (minimum + ".0.0").split(".")[:3])):
            raise ValueError("Binary minimum OS exceeds declared target")
        maximum = max(maximum, min_value)
        bases = [
            expand(r, path.parent, executable.parent, app) for r in selected["rpaths"]
        ]
        bases += [
            expand(r, executable.parent, executable.parent, app)
            for r in exe_slice["rpaths"]
        ]
        for dependency in selected["dependencies"]:
            if dependency.startswith(("/usr/lib/", "/System/Library/")):
                if posixpath.normpath(dependency) != dependency or "\\" in dependency:
                    raise ValueError("Noncanonical system dependency")
                continue
            if dependency.startswith("@rpath/"):
                candidates = [
                    inside(app, base / dependency[len("@rpath/") :]) for base in bases
                ]
            else:
                candidates = [expand(dependency, path.parent, executable.parent, app)]
            if not any(
                c in paths and any(s["architecture"] == architecture for s in paths[c])
                for c in candidates
            ):
                raise ValueError("Unresolved bundled dynamic dependency")
        binaries.append(
            dict(
                path=path.relative_to(app).as_posix(),
                sha256=sha256(path.read_bytes()).hexdigest(),
                **selected,
            )
        )
    display = subprocess.run(
        ["/usr/bin/codesign", "-d", "--verbose=4", str(app)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    verify = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        capture_output=True,
        timeout=60,
    )
    team = next(
        (
            line.split("=", 1)[1]
            for line in display.stderr.splitlines()
            if line.startswith("TeamIdentifier=")
        ),
        None,
    )
    adhoc = "Signature=adhoc" in display.stderr or "adhoc" in display.stderr
    signing = dict(
        state="ad_hoc"
        if adhoc
        else (
            "identified"
            if display.returncode == 0 and team not in {None, "not set"}
            else "unsigned"
        ),
        cryptographicVerification=verify.returncode == 0,
        notarization="not_checked",
        distributionApproved=False,
    )
    tree = {
        p.relative_to(app).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in sorted(app.rglob("*"))
        if p.is_file()
    }
    return dict(
        packagingSourceSha256=build.get("packagingSourceSha256"),
        sourceVerified=root is not None,
        appTreeSha256=sha256(json.dumps(tree, sort_keys=True).encode()).hexdigest(),
        schema=1,
        stage="P8-C-artifact-audit",
        status="passed",
        platform="macos-" + architecture,
        declaredMinimumMacOS=minimum,
        maximumBinaryMinimumMacOS=".".join(map(str, maximum)),
        minimumMacOSActuallyTested=False,
        payloadFiles=len(files),
        nativeBinaries=binaries,
        signature=signing,
        licenseReview="pending",
        releaseReady=False,
    )
