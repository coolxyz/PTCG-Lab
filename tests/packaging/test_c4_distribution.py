"""C4 rejects false signing/clean-machine claims and preserves hash closures."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import jsonschema
import pytest

from scripts.packaging.c4_signing import commands, identified, reseal
from scripts.packaging.c4_dmg import launcher
from scripts.packaging.c4_windows import configuration, HOOK
from scripts.packaging.c4_notary import plan
from scripts.packaging.c4_matrix import template, validate

ROOT = Path(__file__).resolve().parents[2]


def test_adhoc_signing_orders_nested_code_and_never_uses_credentials():
    result = commands(
        "/new/APP.app",
        ["Contents/MacOS/shell", "Contents/Resources/backend/python/bin/python3"],
    )
    assert result[0][-1].endswith("python3") and result[-1][-1].endswith("/shell")
    assert all(
        c[1:4] == ["--force", "--sign", "-"]
        and "--deep" not in c
        and "--timestamp" not in c
        for c in result
    )


@pytest.mark.parametrize(
    "value", [None, "Developer ID Application: Name", "-", "a" * 39, "a" * 41, "x" * 40]
)
def test_developer_id_requires_explicit_valid_fingerprint(value):
    with pytest.raises(ValueError):
        commands("/new", [], mode="developer-id", identity=value)


def test_developer_id_enables_timestamp_and_hardened_runtime():
    result = commands("/new", ["binary"], mode="developer-id", identity="a" * 40)[0]
    assert (
        "--timestamp" in result and result[result.index("--options") + 1] == "runtime"
    )
    with pytest.raises(ValueError):
        commands("/new", [], identity="a" * 40)


VALID = "CodeDirectory v=20500 flags=0x10000(runtime) hashes=4\nAuthority=Developer ID Application: Example (ABCDEFGHIJ)\nTeamIdentifier=ABCDEFGHIJ\nTimestamp=Oct 28, 2026\n"


@pytest.mark.parametrize(
    "kind",
    [
        "adhoc",
        "wrongTeam",
        "missingTimestamp",
        "noneTimestamp",
        "runtimeOff",
        "fakeRuntime",
        "wrongAuthority",
    ],
)
def test_unaccepted_signatures_cannot_be_promoted(kind):
    value = VALID
    if kind == "adhoc":
        value += "Signature=adhoc\n"
    elif kind == "wrongTeam":
        value = value.replace("TeamIdentifier=ABCDEFGHIJ", "TeamIdentifier=OTHERTEAM1")
    elif kind == "missingTimestamp":
        value = value.split("Timestamp=")[0]
    elif kind == "noneTimestamp":
        value = value.replace("Oct 28, 2026", "none")
    elif kind == "runtimeOff":
        value = value.replace("flags=0x10000", "flags=0x0")
    elif kind == "fakeRuntime":
        value = value.replace("CodeDirectory", "SomeCertificateName")
    else:
        value = value.replace(
            "Authority=Developer ID Application:", "Authority=Apple Development:"
        )
    assert identified(VALID, "ABCDEFGHIJ") and not identified(value, "ABCDEFGHIJ")


def test_signed_binary_reseals_payload_inventory_and_spdx_before_outer_signature(
    tmp_path,
):
    app = tmp_path / "New.app"
    p = app / "Contents/Resources/backend"
    d = app / "Contents/Resources/distribution"
    p.mkdir(parents=True)
    d.mkdir(parents=True)
    (p / "binary").write_bytes(b"after-signing")
    (p / "manifest.json").write_text(json.dumps({"files": {"binary": "old-hash"}}))
    (d / "COMPONENTS.json").write_text(
        json.dumps(
            {
                "payloadManifestSha256": "old",
                "licenseReview": "pending",
                "components": [],
            }
        )
    )
    (d / "SBOM.spdx.json").write_text(
        json.dumps({"documentNamespace": "old", "spdxVersion": "SPDX-2.3"})
    )
    reseal(app, {"mode": "adhoc", "releaseReady": False})
    m = json.loads((p / "manifest.json").read_text())
    inv = json.loads((d / "COMPONENTS.json").read_text())
    assert m["files"]["binary"] == sha256(b"after-signing").hexdigest()
    assert (
        inv["payloadManifestSha256"]
        == sha256((p / "manifest.json").read_bytes()).hexdigest()
    )
    assert json.loads((d / "SBOM.spdx.json").read_text())["documentNamespace"].endswith(
        sha256(json.dumps(inv, sort_keys=True).encode()).hexdigest()
    )
    assert (
        inv["licenseReview"] == "pending"
        and json.loads((d / "SIGNING-TRANSFORM.json").read_text())["releaseReady"]
        is False
    )


@pytest.mark.parametrize("value", ["x" * 64, "a" * 63, "a" * 65, "a" * 63 + ";"])
def test_launcher_refuses_unpinned_or_shell_input(value):
    with pytest.raises(ValueError):
        launcher(value)


def test_installer_launcher_is_quoted_and_only_delegates_to_c3():
    text = launcher("a" * 64)
    subprocess.run(["/bin/sh", "-n"], input=text, text=True, check=True)
    assert '"$C4_APP/Contents/Resources/backend/python/bin/python3" -I -B' in text
    assert (
        "--adopt" in text
        and "sudo" not in text
        and "xattr" not in text
        and "/usr/bin/open" not in text
    )


def test_windows_installer_schema_and_pending_migration_are_explicit():
    c = configuration(Path("/approved/backend"), Path("/prepared/hooks.nsh"))
    jsonschema.validate(
        c,
        json.loads(
            (ROOT / "node_modules/@tauri-apps/cli/config.schema.json").read_text()
        ),
    )
    assert c["bundle"]["active"] is False
    assert c["bundle"]["windows"]["nsis"]["installMode"] == "currentUser"
    assert c["bundle"]["windows"]["allowDowngrades"] is False
    assert c["bundle"]["windows"]["webviewInstallMode"] == {"type": "skip"}
    assert "Abort" in HOOK and "RMDir" not in HOOK and "Delete" not in HOOK


@pytest.mark.parametrize(
    "value", ["secret/path", "profile name", "", "x" * 65, "name;run"]
)
def test_notary_plan_refuses_credentials_or_command_text(value):
    with pytest.raises(ValueError):
        plan("/new/A.app", "/new/A.dmg", profile=value)


def test_notary_plan_has_app_ticket_before_dmg_and_never_executes():
    p = plan("/new/A.app", "/new/A.dmg", profile="approved-keychain-label")
    assert p["executed"] is False and p["externalApprovalRequired"] is True
    assert p["appStage"][1][1:3] == ["notarytool", "submit"]
    assert p["appStage"][2][1:3] == ["stapler", "staple"]
    assert "recompute-stapled-APP-tree-and-bundle-identities" in p["betweenStages"]
    assert p["dmgStage"][0][3] == "/new/A.dmg" and p["releaseReady"] is False


@pytest.mark.parametrize("mode", ["automatic", "self-signed"])
def test_unknown_signing_mode_never_selects_a_certificate(mode):
    with pytest.raises(ValueError):
        commands("/new", [], mode=mode)


def test_later_minor_os_is_not_the_declared_minimum(tmp_path):
    m, e = evidence(tmp_path, "minimumOS")
    e["facts"]["minimumOSActuallyTested"] = True
    e["facts"]["osVersion"] = "12.7.1"
    raw = json.dumps(e).encode()
    (tmp_path / "proof.json").write_bytes(raw)
    m["targets"]["macos-arm64"]["cases"]["minimumOS"]["sha256"] = sha256(
        raw
    ).hexdigest()
    assert not validate(m, tmp_path)


def evidence(tmp_path, case="installMedia"):
    m = template("a" * 64)
    e = {
        "status": "passed",
        "facts": {
            "platform": "macos-arm64",
            "cleanMachineAttested": False,
            "minimumOSActuallyTested": False,
            "osVersion": "26.0",
        },
        "wrapperSourceSha256": "a" * 64,
        "artifactSha256": "b" * 64,
        "acceptedCases": [case],
    }
    raw = json.dumps(e).encode()
    (tmp_path / "proof.json").write_bytes(raw)
    m["targets"]["macos-arm64"]["cases"][case] = {
        "status": "passed",
        "evidence": "proof.json",
        "sha256": sha256(raw).hexdigest(),
        "artifactSha256": "b" * 64,
    }
    return m, e


def test_pending_matrix_and_bound_local_media_evidence(tmp_path):
    assert validate(template("a" * 64), tmp_path)
    m, _ = evidence(tmp_path)
    assert validate(m, tmp_path)


@pytest.mark.parametrize("case", ["minimumOS", "cleanStandardUser"])
def test_developer_host_is_not_minimum_os_or_clean_machine(tmp_path, case):
    m, _ = evidence(tmp_path, case)
    assert not validate(m, tmp_path)


@pytest.mark.parametrize(
    "kind",
    [
        "proofHash",
        "artifact",
        "source",
        "platform",
        "missingTarget",
        "missingCase",
        "path",
        "release",
    ],
)
def test_matrix_rejects_unbound_or_incomplete_claims(tmp_path, kind):
    m, _ = evidence(tmp_path)
    row = m["targets"]["macos-arm64"]["cases"]["installMedia"]
    if kind == "proofHash":
        row["sha256"] = "0" * 64
    elif kind == "artifact":
        row["artifactSha256"] = "0" * 64
    elif kind == "source":
        m["wrapperSourceSha256"] = "0" * 64
    elif kind == "platform":
        m["targets"]["windows-x64"]["cases"]["installMedia"] = deepcopy(row)
    elif kind == "missingTarget":
        del m["targets"]["windows-x64"]
    elif kind == "missingCase":
        del m["targets"]["macos-arm64"]["cases"]["minimumOS"]
    elif kind == "path":
        row["evidence"] = "../outside"
    else:
        m["releaseReady"] = True
    assert not validate(m, tmp_path)
