"""Synthetic publication failures; never use actual credentials or private data."""

import hashlib
import json
import subprocess
import zipfile

import pytest
from scripts.public.policy import source_files
from scripts.public.export_source import export_source
from scripts.public.audit import audit_archive, audit_worktree


def put(root, name, value="public sample"):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value)
    return p


@pytest.mark.parametrize(
    "name",
    [
        "apps/online-server/pyproject.toml",
        "packages/battle-protocol/src/ptcg_protocol/schema/scaffold.schema.json",
        "packages/battle-ui/src/index.js",
        "ops/online/Dockerfile",
        "ops/online/.dockerignore",
        "ops/online/compose.yaml",
        "ops/online/.env.example",
        ".github/workflows/publication-gate.yml",
        "tests/synthetic/protocol/scaffold.json",
    ],
)
def test_new_projects_and_explicit_synthetic_fixtures_are_exported(tmp_path, name):
    put(tmp_path, name)
    assert name in source_files(tmp_path)


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        "ops/online/.env",
        "ops/online/secrets/db_password",
        "packages/battle-core/var/personal.json",
        "packages/battle-core/src/data/cards.json",
        "packages/battle-core/dist/private.json",
        "apps/desktop-api/private/session.json",
        "tests/fixtures/cards.json",
        "tests/synthetic/protocol/unreviewed.json",
        "tests/real.json",
        "data/catalog/catalog.json",
        "runtime/engine/engine.py",
        "apps/online-server/credentials.json",
        "ops/online/server.key",
    ],
)
def test_private_paths_never_export(tmp_path, name):
    put(tmp_path, name, "private-sentinel")
    assert name not in source_files(tmp_path)


def test_symlink_file_and_parent_refused(tmp_path):
    outside = tmp_path / "outside"
    put(outside, "secret.py")
    root = tmp_path / "root"
    (root / "packages/core").mkdir(parents=True)
    (root / "packages/core/api.py").symlink_to(outside / "secret.py")
    with pytest.raises(ValueError, match="Symlink"):
        source_files(root)
    (root / "packages/core/api.py").unlink()
    (root / "packages/core/src").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        source_files(root)


@pytest.mark.parametrize(
    "value", ["gh" + "p_" + "a" * 40, "/" + "Users/" + "private-person/record"]
)
def test_secret_and_machine_path_block_before_output_without_echo(tmp_path, value):
    root = tmp_path / "root"
    put(root, "apps/online-server/src/new.py", value)
    output = tmp_path / "output.zip"
    with pytest.raises(ValueError) as exc:
        export_source(output, root)
    assert value not in str(exc.value)
    assert not output.exists()
    assert not output.with_name("output.zip.sha256").exists()


def test_untracked_is_scanned(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    put(tmp_path, "README.md", "tracked")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    value = "gh" + "p_" + "b" * 40
    put(tmp_path, "apps/online-server/src/new.py", value)
    result = audit_worktree(tmp_path)
    assert not result["currentTrackedFindings"]
    assert result["currentUntrackedFindings"][0]["categories"] == ["credential_pattern"]
    assert value not in json.dumps(result)


def test_export_manifest_and_no_overwrite(tmp_path):
    root = tmp_path / "root"
    put(root, "README.md", "clean source")
    put(root, "var/private.json", "private-sentinel")
    output = tmp_path / "source.zip"
    export_source(output, root)
    assert audit_archive(output) == []
    before = output.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        export_source(output, root)
    assert output.read_bytes() == before
    with zipfile.ZipFile(output) as archive:
        assert "PTCG-Lab/var/private.json" not in archive.namelist()


def archive_with(path, name, content, manifest=None):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("PTCG-Lab/" + name, content)
        archive.writestr(
            "PTCG-Lab/PUBLIC-SOURCE.json",
            json.dumps(
                manifest
                or {
                    "schema": 1,
                    "containsGitHistory": False,
                    "containsRuntimeData": False,
                    "files": {name: hashlib.sha256(content).hexdigest()},
                }
            ),
        )


@pytest.mark.parametrize(
    "name",
    [
        "../escape.py",
        "/absolute.py",
        "var/private.json",
        "ops/online/secrets/password.json",
    ],
)
def test_artifact_rejects_forged_private_paths(tmp_path, name):
    output = tmp_path / "bad.zip"
    archive_with(output, name, b"private-sentinel")
    assert audit_archive(output)


def test_artifact_detects_tamper_and_missing_file(tmp_path):
    output = tmp_path / "bad.zip"
    archive_with(
        output,
        "README.md",
        b"changed",
        {
            "schema": 1,
            "containsGitHistory": False,
            "containsRuntimeData": False,
            "files": {"README.md": "0" * 64, "apps/online-server/missing.py": "1" * 64},
        },
    )
    issues = {x for finding in audit_archive(output) for x in finding["categories"]}
    assert {"checksum_mismatch", "manifest_file_set_mismatch"} <= issues


def test_manifest_content_is_also_scanned(tmp_path):
    output = tmp_path / "bad.zip"
    value = "gh" + "p_" + "d" * 40
    archive_with(
        output,
        "README.md",
        b"clean",
        {
            "schema": 1,
            "containsGitHistory": False,
            "containsRuntimeData": False,
            "files": {"README.md": hashlib.sha256(b"clean").hexdigest()},
            "note": value,
        },
    )
    findings = audit_archive(output)
    assert any("credential_pattern" in f["categories"] for f in findings)
    assert value not in json.dumps(findings)
