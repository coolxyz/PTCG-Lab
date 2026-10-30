"""Synthetic artifact gates; never constitute minimum-OS or signed-release proof."""

from hashlib import sha256
import json
import plistlib
import struct
from types import SimpleNamespace

import pytest
from scripts.packaging import desktop_audit as a, desktop_inventory as inv


def command(kind, string):
    offset = 12 if kind == 0x8000001C else 24
    data = string.encode() + b"\0"
    size = (offset + len(data) + 7) // 8 * 8
    return (
        struct.pack("<III", kind, size, offset)
        + bytes(offset - 12)
        + data
        + bytes(size - offset - len(data))
    )


def binary(*, arch=0x100000C, minimum=(12 << 16), deps=(), rpaths=()):
    commands = [
        struct.pack("<6I", 0x32, 24, 1, minimum, minimum, 0),
        *[command(0xC, d) for d in deps],
        *[command(0x8000001C, d) for d in rpaths],
    ]
    return struct.pack(
        "<8I", 0xFEEDFACF, arch, 0, 2, len(commands), sum(map(len, commands)), 0, 0
    ) + b"".join(commands)


def seal(payload, platform="macos-arm64"):
    manifest = dict(
        schema=1,
        platform=platform,
        python="python/bin/python3",
        files={
            p.relative_to(payload).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in payload.rglob("*")
            if p.is_file() and p.name != "manifest.json"
        },
    )
    (payload / "manifest.json").write_text(json.dumps(manifest))


def write(root, name, raw):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw.encode() if isinstance(raw, str) else raw)
    return p


def fixture(tmp_path, monkeypatch):
    app = tmp_path / "Preview.app"
    payload = app / "Contents/Resources/backend"
    write(app, "Contents/Info.plist", plistlib.dumps({"CFBundleExecutable": "native"}))
    write(
        app,
        "Contents/MacOS/native",
        binary(
            deps=("@rpath/libthing.dylib", "/usr/lib/libSystem.B.dylib"),
            rpaths=("@executable_path/../Frameworks",),
        ),
    )
    write(app, "Contents/Frameworks/libthing.dylib", binary())
    write(payload, "python/bin/python3", binary())
    write(payload, "bootstrap.py", "# synthetic")
    seal(payload)
    distribution = app / "Contents/Resources/distribution"
    write(distribution, "licenses/example/LICENSE", "synthetic license")
    components = [dict(id="SPDXRef-example")]
    write(
        distribution,
        "BUILD-SOURCE.json",
        json.dumps(
            {
                "schema": 1,
                "rulePolicy": "current-only-v1",
                "packagingSourceSha256": "0" * 64,
            }
        ),
    )
    write(
        distribution,
        "COMPONENTS.json",
        json.dumps(
            {
                "components": components,
                "payloadManifestSha256": sha256(
                    (payload / "manifest.json").read_bytes()
                ).hexdigest(),
                "licenseFiles": [
                    {
                        "path": "licenses/example/LICENSE",
                        "sha256": sha256(b"synthetic license").hexdigest(),
                    }
                ],
            }
        ),
    )
    write(
        distribution,
        "SBOM.spdx.json",
        json.dumps(
            {"spdxVersion": "SPDX-2.3", "packages": [{"SPDXID": "SPDXRef-example"}]}
        ),
    )
    monkeypatch.setattr(
        a.subprocess,
        "run",
        lambda *args, **kw: SimpleNamespace(
            returncode=0, stderr="Signature=adhoc\nTeamIdentifier=not set"
        ),
    )
    return app, payload, distribution


def test_audit_is_relocatable_and_never_grants_release(tmp_path, monkeypatch):
    app, _, _ = fixture(tmp_path, monkeypatch)
    result = a.audit(app)
    assert len(result["nativeBinaries"]) == 3
    assert result["maximumBinaryMinimumMacOS"] == "12.0.0"
    assert result["signature"]["state"] == "ad_hoc"
    assert (
        result["signature"]["distributionApproved"] is result["releaseReady"] is False
    )
    assert result["minimumMacOSActuallyTested"] is False


@pytest.mark.parametrize(
    "kind",
    [
        "foreign",
        "minimum",
        "external",
        "escape",
        "unresolved",
        "system-escape",
        "license",
        "sbom",
        "payload",
        "extra",
        "link",
        "32bit",
    ],
)
def test_audit_rejects_unsafe_incomplete_or_wrong_target(tmp_path, monkeypatch, kind):
    app, payload, dist = fixture(tmp_path, monkeypatch)
    target = app / "Contents/MacOS/native"
    if kind == "foreign":
        target.write_bytes(binary(arch=0x1000007))
    elif kind == "minimum":
        target.write_bytes(binary(minimum=26 << 16))
    elif kind == "external":
        target.write_bytes(binary(deps=("/opt/unbundled/lib.dylib",)))
    elif kind == "escape":
        target.write_bytes(binary(rpaths=("@loader_path/../../../../outside",)))
    elif kind == "unresolved":
        target.write_bytes(binary(deps=("@rpath/missing.dylib",)))
    elif kind == "system-escape":
        target.write_bytes(binary(deps=("/usr/lib/../../opt/unbundled/lib.dylib",)))
    elif kind == "license":
        (dist / "licenses/example/LICENSE").write_text("changed")
    elif kind == "sbom":
        (dist / "SBOM.spdx.json").write_text('{"spdxVersion":"SPDX-2.3","packages":[]}')
    elif kind == "payload":
        (payload / "bootstrap.py").write_text("changed")
    elif kind == "extra":
        write(payload, "unlisted.py", "extra")
    elif kind == "link":
        (app / "Contents/alias").symlink_to(target)
    else:
        target.write_bytes(b"\xce\xfa\xed\xfe" + bytes(28))
    with pytest.raises(ValueError):
        a.audit(app)


@pytest.mark.parametrize(
    "kind",
    [
        "short",
        "command-size",
        "string-offset",
        "missing-null",
        "wrong-platform",
        "commands",
        "fat-range",
        "fat-arch",
    ],
)
def test_macho_parser_bounds_reject_invalid_commands(kind):
    raw = bytearray(binary(rpaths=("@loader_path",)))
    if kind == "short":
        raw = raw[:12]
    elif kind == "command-size":
        struct.pack_into("<I", raw, 60, 0)
    elif kind == "string-offset":
        struct.pack_into("<I", raw, 64, 4096)
    elif kind == "missing-null":
        raw[68:] = b"x" * len(raw[68:])
    elif kind == "wrong-platform":
        struct.pack_into("<I", raw, 40, 2)
    elif kind == "commands":
        struct.pack_into("<I", raw, 16, 100)
    elif kind == "fat-range":
        raw = bytearray(struct.pack(">7I", 0xCAFEBABE, 1, 0x100000C, 0, 65536, 200, 0))
    else:
        body = binary(arch=0x1000007)
        raw = bytearray(
            struct.pack(">7I", 0xCAFEBABE, 1, 0x100000C, 0, 28, len(body), 0) + body
        )
    with pytest.raises(ValueError):
        a.macho(bytes(raw))


def inventory_fixture(tmp_path):
    root = tmp_path / "source"
    payload = tmp_path / "payload"
    write(
        root,
        "scripts/native/runtime-lock.json",
        json.dumps(
            {
                "python": "3.14.10",
                "url": "https://example.invalid/runtime",
                "sha256": "a" * 64,
            }
        ),
    )
    write(root, "apps/native-shell/src-tauri/Cargo.lock", "version=4\npackage=[]")
    for project in ["apps/web", "apps/desktop-web"]:
        write(root, project + "/package.json", '{"dependencies":{"react":"19.1.1"}}')
    write(
        root,
        "package-lock.json",
        '{"packages":{"node_modules/react":{"version":"19.1.1"}}}',
    )
    write(
        root,
        "node_modules/react/package.json",
        '{"name":"react","version":"19.1.1","license":"MIT"}',
    )
    write(root, "node_modules/react/LICENSE", "synthetic npm license")
    write(root, "LICENSE", "synthetic own license")
    write(root, "THIRD_PARTY_NOTICES.md", "synthetic notes")
    write(payload, "python/bin/python3", "synthetic")
    write(payload, "python/lib/python3.14/LICENSE.txt", "synthetic runtime license")
    write(
        payload,
        "python/lib/python3.14/site-packages/example-1.dist-info/METADATA",
        "Metadata-Version: 2.4\nName: example\nVersion: 1\nLicense-Expression: MIT\nLicense-File: LICENSE\n",
    )
    write(
        payload,
        "python/lib/python3.14/site-packages/example-1.dist-info/licenses/LICENSE",
        "synthetic wheel license",
    )
    write(payload, "app/runtime/engine/UPSTREAM-LICENSE", "synthetic engine license")
    seal(payload)
    return root, payload


def test_sbom_preserves_actual_licenses_and_scope(tmp_path):
    root, payload = inventory_fixture(tmp_path)
    result = inv.generate(
        payload, tmp_path / "distribution", root=root, cargo_home=tmp_path / "cargo"
    )
    assert (
        result["licenseReview"] == "pending"
        and result["redistributionApproved"] is False
    )
    assert len(result["components"]) == 6
    assert len(result["licenseFiles"]) == 5
    sbom = json.loads((tmp_path / "distribution/SBOM.spdx.json").read_text())
    assert sbom["spdxVersion"] == "SPDX-2.3"
    assert len(sbom["packages"]) == 6
    assert any(p["licenseDeclared"] == "MIT" for p in sbom["packages"])
    assert all(p["licenseConcluded"] == "NOASSERTION" for p in sbom["packages"])


@pytest.mark.parametrize("kind", ["metadata-path", "duplicate", "npm-version", "state"])
def test_inventory_cannot_hide_unknown_inputs(tmp_path, kind):
    root, payload = inventory_fixture(tmp_path)
    if kind == "metadata-path":
        write(
            payload,
            "python/lib/python3.14/site-packages/example-1.dist-info/METADATA",
            "Name: example\nVersion: 1\nLicense-File: ../../secret\n",
        )
        seal(payload)
    elif kind == "duplicate":
        write(
            payload,
            "python/lib/python3.14/site-packages/duplicate-1.dist-info/METADATA",
            "Name: example\nVersion: 1\n",
        )
        seal(payload)
    elif kind == "npm-version":
        write(
            root,
            "node_modules/react/package.json",
            '{"name":"react","version":"0.0.1"}',
        )
    else:
        write(payload, "connection.json", '{"private":"synthetic"}')
        seal(payload)
    with pytest.raises(ValueError):
        inv.generate(
            payload, tmp_path / "rejected", root=root, cargo_home=tmp_path / "cargo"
        )


def test_parser_wheel_mismatch_fails_before_repair(tmp_path, monkeypatch):
    from scripts.packaging.prepare_macos import prepare, PILLOW_FILE

    root, payload = inventory_fixture(tmp_path)
    write(
        root,
        "scripts/native/windows-wheel-lock.json",
        '{"wheels":[{"name":"mwparserfromhell","filename":"parser.whl","sha256":"bad"}]}',
    )
    before = write(payload, PILLOW_FILE, binary())
    seal(payload)
    wheel = write(tmp_path, "parser.whl", b"untrusted")
    monkeypatch.setattr(
        a.subprocess,
        "run",
        lambda *args, **kw: pytest.fail("Repair must not run on unverified inputs"),
    )
    original = before.read_bytes()
    with pytest.raises(ValueError, match="Pinned"):
        prepare(payload, wheel, root=root)
    assert before.read_bytes() == original
