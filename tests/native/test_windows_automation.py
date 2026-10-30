"""Synthetic rejection tests only; these never qualify as Windows execution."""

from hashlib import sha256
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

import pytest
from scripts.native import windows_evidence as evidence
from scripts.packaging.source_identity import source_digest
from scripts.native.windows_environment import eligible
from scripts.native.windows_pipeline import new_run
from scripts.native.desktop_smoke import ready, CHECKS
from scripts.native.windows_report import REQUIRED as PYTHON_CASES, summarize
from scripts.native.windows_shell_check import REQUIRED as RUST_CASES

ROOT = Path(__file__).resolve().parents[2]


def environment():
    return dict(
        schema=1,
        sysPlatform="win32",
        osName="nt",
        nativeMachine=0x8664,
        processMachine=0,
        pointerBits=64,
        python="3.14.10",
        wine=False,
        adminMember=False,
        elevated=False,
        elevationType=1,
        standardUser=True,
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("sysPlatform", "linux"),
        ("osName", "posix"),
        ("nativeMachine", 0xAA64),
        ("processMachine", 0x14C),
        ("pointerBits", 32),
        ("python", "3.14.9"),
        ("wine", True),
        ("adminMember", True),
        ("elevated", True),
        ("elevationType", 3),
        ("standardUser", "true"),
    ],
)
def test_native_preflight_rejects_other_environment(key, value):
    model = environment()
    assert eligible(model)
    model[key] = value
    assert not eligible(model)


def test_output_refuses_existing_ancestor_and_link(tmp_path):
    with pytest.raises(ValueError):
        new_run(tmp_path)
    target = tmp_path / "new"
    target.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        new_run(target / "output")
    created = new_run(tmp_path / "isolated")
    assert created.is_dir()


def test_gui_private_frames_require_exact_origin_checks_and_nonce():
    model = dict(
        schema=1,
        type="gui-ready",
        nonce="a" * 64,
        backendPid=123,
        origin="http://127.0.0.1:23456",
        checks={k: True for k in CHECKS},
    )
    assert ready(model, "a" * 64) == 23456
    for change in (
        {"origin": "http://localhost:23456"},
        {"origin": "http://127.0.0.1:23456/?secret"},
        {"cookie": "forbidden"},
        {"nonce": "b" * 64},
        {"checks": {k: 1 for k in CHECKS}},
    ):
        with pytest.raises(ValueError):
            ready({**model, **change}, "a" * 64)


def synthetic_run(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()

    def write(name, value):
        p = run / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value), encoding="utf-8")

    digest = source_digest(ROOT)

    def stage(stage, **kwargs):
        return dict(
            schema=1,
            stage=stage,
            status="passed",
            sourceSha256=digest,
            releaseReady=False,
            **kwargs,
        )

    write("environment.json", environment())
    xml = ET.Element("testsuites")
    suite = ET.SubElement(xml, "testsuite")
    for name in PYTHON_CASES:
        ET.SubElement(suite, "testcase", name=name)
    (run / "python").mkdir()
    ET.ElementTree(xml).write(run / "python/results.xml")
    write(
        "python/report.json",
        stage("P8-B-components", python="3.14.10", tests=len(PYTHON_CASES)),
    )
    (run / "rust.log").write_text(
        "\n".join(
            [
                *(f"test {name} ... ok" for name in sorted(RUST_CASES)),
                f"test result: ok. {len(RUST_CASES)} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;",
            ]
        ),
        encoding="utf-8",
    )
    write(
        "rust.json",
        stage(
            "P8-B-shell-components",
            tests=len(RUST_CASES),
            nativeShellCompile="passed",
            releaseBuild="passed",
        ),
    )
    raw = bytearray(128)
    raw[:2] = b"MZ"
    struct.pack_into("<I", raw, 60, 64)
    raw[64:68] = b"PE\0\0"
    struct.pack_into("<H", raw, 68, 0x8664)
    (run / "bundle").mkdir()
    (run / "bundle/PTCG Lab Preview.exe").write_bytes(raw)
    write(
        "gui/report.json",
        stage(
            "P8-B-desktop-smoke",
            platform="windows-x64",
            executableSha256=sha256(raw).hexdigest(),
            checks={k: True for k in evidence.GUI_CHECKS},
        ),
    )
    write(
        "bundle/BACKEND-PROBE.json",
        stage(
            "P8-B-native-payload",
            externalWheels=37,
            internalWheels=4,
            checks={k: True for k in evidence.PAYLOAD_CHECKS},
        ),
    )
    write("bundle/BACKEND-OWNER-PROBE.json", {k: True for k in evidence.OWNER_CHECKS})
    write("bundle/backend/BUILD-SOURCE.json", {"nativeSourceSha256": digest})
    (run / "bundle/backend/python").mkdir()
    (run / "bundle/backend/python/python.exe").write_bytes(raw)
    (run / "bundle/backend/bootstrap.py").write_bytes(b"# synthetic fixture")
    files = {
        p.relative_to(run / "bundle/backend").as_posix(): sha256(
            p.read_bytes()
        ).hexdigest()
        for p in (run / "bundle/backend").rglob("*")
        if p.is_file()
    }
    write(
        "bundle/backend/manifest.json",
        dict(schema=1, platform="windows-x64", python="python/python.exe", files=files),
    )
    monkeypatch.setattr(
        "scripts.packaging.common.provenance",
        lambda: {"publicSourceSha256": "synthetic-source"},
    )
    files = {
        p.relative_to(run / "bundle").as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in (run / "bundle").rglob("*")
        if p.is_file()
    }
    write(
        "bundle/MANIFEST.json",
        dict(
            kind="ptcg-windows-preview",
            platform="windows-x64",
            releaseReady=False,
            source={"publicSourceSha256": "synthetic-source"},
            files=files,
        ),
    )
    return run


def test_evidence_keeps_manual_acceptance_pending(tmp_path, monkeypatch):
    run = synthetic_run(tmp_path, monkeypatch)
    result = evidence.collect(run, root=ROOT)
    assert result["status"] == "automated_passed"
    assert result["p8bComplete"] is result["releaseReady"] is False
    assert set(result["manualAcceptance"].values()) == {"pending"}


@pytest.mark.parametrize(
    "kind",
    [
        "stale",
        "mac",
        "string-bool",
        "exe",
        "payload",
        "extra",
        "missing",
        "skipped",
        "admin",
        "duplicate",
    ],
)
def test_evidence_refuses_stale_incomplete_or_tampered_proofs(
    tmp_path, monkeypatch, kind
):
    run = synthetic_run(tmp_path, monkeypatch)
    if kind in {"stale", "mac", "string-bool"}:
        path = run / "gui/report.json"
        value = json.loads(path.read_text())
        if kind == "stale":
            value["sourceSha256"] = "0" * 64
        elif kind == "mac":
            value["platform"] = "macos-arm64"
        else:
            value["checks"]["actualWebview"] = "true"
        path.write_text(json.dumps(value))
    elif kind in {"exe", "payload"}:
        (
            run
            / (
                "bundle/PTCG Lab Preview.exe"
                if kind == "exe"
                else "bundle/backend/bootstrap.py"
            )
        ).write_bytes(b"changed")
    elif kind == "extra":
        (run / "bundle/extra.txt").write_bytes(b"extra")
    elif kind == "missing":
        (run / "rust.json").unlink()
    elif kind in {"skipped", "duplicate"}:
        path = run / "python/results.xml"
        tree = ET.parse(path)
        case = tree.find(".//testcase")
        if kind == "skipped":
            ET.SubElement(case, "skipped")
        else:
            tree.find("testsuite").append(ET.fromstring(ET.tostring(case)))
        tree.write(path)
        with pytest.raises(ValueError):
            summarize(path)
    else:
        value = environment()
        value["elevationType"] = 3
        (run / "environment.json").write_text(json.dumps(value))
    with pytest.raises((ValueError, OSError)):
        evidence.collect(run, root=ROOT)


def test_full_pipeline_refuses_platform_before_writing(tmp_path, monkeypatch):
    from scripts.native import windows_pipeline as runner

    value = environment()
    value["elevated"] = True
    monkeypatch.setattr(runner, "facts", lambda: value)
    output = tmp_path / "not-created"
    with pytest.raises(ValueError, match="non-admin"):
        runner.pipeline(tmp_path, output)
    assert not output.exists()


def test_full_pipeline_failure_cannot_leave_success_or_resume(tmp_path, monkeypatch):
    from scripts.native import windows_pipeline as runner

    monkeypatch.setattr(runner, "facts", environment)
    monkeypatch.setattr(
        runner.subprocess, "check_output", lambda *a, **kw: "rustc 1.90.0 (synthetic)"
    )
    monkeypatch.setattr(runner.shutil, "which", lambda *a: "npm.cmd")

    def fail(*a, **kw):
        raise runner.subprocess.CalledProcessError(7, ["synthetic-failure"])

    monkeypatch.setattr(runner.subprocess, "run", fail)
    output = tmp_path / "isolated-run"
    with pytest.raises(runner.subprocess.CalledProcessError):
        runner.pipeline(tmp_path, output)
    result = json.loads((output / "FAILED.json").read_text())
    assert result["status"] == "failed" and result["stage"] == "prepare-inputs"
    assert result["releaseReady"] is False and not (output / "REPORT.json").exists()
    with pytest.raises(ValueError, match="NEW"):
        runner.pipeline(tmp_path, output)
