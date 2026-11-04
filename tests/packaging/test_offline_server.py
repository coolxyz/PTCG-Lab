"""An offline installer configures new isolated state, never a copied user's kit."""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

from scripts.packaging.api_server import compose_document
from scripts.packaging.offline_setup import setup
from scripts.packaging import offline_server


def package(tmp_path):
    root, state = tmp_path / "package", tmp_path / "state"
    root.mkdir(); state.mkdir(mode=0o700)
    (root / "IMAGES.json").write_text(json.dumps({
        "schema": 1, "kind": "ptcg-offline-api", "platform": "linux/amd64",
        "ruleReleaseId": "current", "caddy": {"tag": "approved-caddy"},
    }))
    base = compose_document("approved-api", "approved-pg", "linux/amd64",
                            "https://unconfigured.invalid", "unconfigured", "current",
                            "a" * 64, 8876, "127.0.0.1")
    (root / "compose.base.json").write_text(json.dumps(base))
    return root, state


def test_first_setup_has_fresh_private_identity_and_pinned_images(tmp_path):
    root, state = package(tmp_path)
    origin = setup(root, state, host="api.example.com", email="operator@example.com")
    target = state / "deployment"
    doc = json.loads((target / "compose.yaml").read_text())
    assert origin == "https://api.example.com"
    assert target.stat().st_mode & 0o777 == 0o700
    assert (target / "private/api.env").stat().st_mode & 0o777 == 0o600
    assert doc["name"].startswith("ptcg-server-")
    assert all(s["pull_policy"] == "never" for s in doc["services"].values())
    assert "ports" not in doc["services"]["api"] and "ports" not in doc["services"]["database"]
    assert doc["services"]["caddy"]["ports"] == ["0.0.0.0:80:80", "0.0.0.0:443:443"]
    for name in ("api", "migrate"):
        env = doc["services"][name]["environment"]
        assert env["PTCG_ONLINE_V2_PUBLIC_ORIGIN"] == origin
        assert env["PTCG_ONLINE_V2_SERVER_ID"].startswith("server-")
        assert env["PTCG_ONLINE_V2_WORKER_RSS_MB"] == "384"
    password = (target / "private/database.env").read_text().split("POSTGRES_PASSWORD=")[1].strip()
    assert len(password) >= 40 and password in (target / "private/api.env").read_text()
    assert password not in (target / "client.json").read_text()
    before = (target / "private/api.env").read_bytes()
    with pytest.raises(ValueError, match="never overwrites"):
        setup(root, state, host="other.example.com", email="operator@example.com")
    assert (target / "private/api.env").read_bytes() == before


def test_invalid_configuration_and_existing_links_are_preserved(tmp_path):
    root, state = package(tmp_path)
    with pytest.raises(ValueError):
        setup(root, state, host="https://bad.example.com", email="operator@example.com")
    assert not list(state.iterdir())
    (state / "deployment").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="retained"):
        setup(root, state, host="api.example.com", email="operator@example.com")
    assert (state / "deployment").is_symlink()


def test_staging_and_nonstandard_ports_keep_consistent_host(tmp_path):
    root, state = package(tmp_path)
    setup(root, state, host="api.example.com", email="operator@example.com",
          port=29443, http_port=29080, bind="127.0.0.1", staging=True,
          subnet="172.30.90.0/28")
    target = state / "deployment"
    assert (target / "origin.txt").read_text().strip() == "https://api.example.com:29443"
    config = (target / "Caddyfile").read_text()
    assert "acme-staging" in config and "Host api.example.com:29443" in config
    assert json.loads((target / "DEPLOYMENT.json").read_text())["acmeStaging"]


def saved_image(path, tag, architecture="arm64"):
    with tarfile.open(path, "w:gz") as tar:
        def add_json(data, name=None):
            payload = json.dumps(data).encode()
            digest = hashlib.sha256(payload).hexdigest()
            member = tarfile.TarInfo(name or "blobs/sha256/" + digest)
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
            return "sha256:" + digest

        config_id = add_json({"architecture": architecture, "os": "linux",
                              "rootfs": {"type": "layers", "diff_ids": []}})
        media_type = "application/vnd.oci.image.manifest.v1+json"
        manifest_id = add_json({"schemaVersion": 2, "mediaType": media_type,
                                "config": {"digest": config_id}, "layers": []})
        add_json({"schemaVersion": 2, "manifests": [
            {"mediaType": media_type, "digest": manifest_id,
             "annotations": {"io.containerd.image.name": "docker.io/library/" + tag}},
        ]}, "index.json")
        add_json([{"Config": "blobs/" + config_id.replace(":", "/"),
                   "RepoTags": [tag], "Layers": []}], "manifest.json")
        return config_id, manifest_id


@pytest.fixture
def installer(tmp_path, monkeypatch):
    kit = tmp_path / "input"
    (kit / "images").mkdir(parents=True)
    (kit / "private").mkdir()
    (kit / "private/api.env").write_text("private-input-sentinel")
    (kit / "client.json").write_text("unrelated-old-server")
    tags = {"api": "ptcg-lab-api-preview:synthetic",
            "postgres": "ptcg-lab-postgres-preview:synthetic", "caddy": "ptcg-lab-caddy:synthetic"}
    images = {tag: saved_image(kit / "images" / (name + ".tar.gz"), tag)[0]
              for name, tag in tags.items()}
    monkeypatch.setattr(offline_server.manage_server, "verify", lambda root: {
        "tlsMode": "acme", "images": images, "platform": "linux/arm64",
        "ruleManifestSha256": "b" * 64, "ruleReleaseId": "current",
    })
    monkeypatch.setattr(offline_server.manage_server, "images_match", lambda value: None)
    monkeypatch.setattr(offline_server.api_server, "verify_api_sources", lambda image: None)
    monkeypatch.setattr(offline_server, "run", lambda *args, **kwargs: "current\n")
    output = tmp_path / "installer with spaces"
    result = offline_server.build(kit, output)
    return output, result


def test_archive_identity_requires_matching_source_tag_and_platform(tmp_path):
    archive = tmp_path / "api.tar.gz"
    tag = "ptcg-lab-api-preview:synthetic"
    config_id, oci_id = saved_image(archive, tag)
    assert offline_server.archive_identity(archive, tag, "linux/arm64", config_id) == {
        "tag": tag, "id": config_id, "manifestId": oci_id,
    }
    with pytest.raises(ValueError, match="image platform mismatch"):
        offline_server.archive_identity(archive, tag, "linux/amd64", config_id)
    with pytest.raises(ValueError, match="image tag mismatch"):
        offline_server.archive_identity(archive, tag + "-other", "linux/arm64", config_id)
    with pytest.raises(ValueError, match="source image ID"):
        offline_server.archive_identity(archive, tag, "linux/arm64", "sha256:" + "0" * 64)


def test_delivery_archive_contains_images_and_no_input_identity_or_secrets(installer):
    output, result = installer
    assert not (output / "private").exists() and not (output / "state").exists()
    assert b"private-input-sentinel" not in b"".join(p.read_bytes() for p in output.rglob("*") if p.is_file())
    assert not (output / "client.json").exists()
    with tarfile.open(result["archive"]) as archive:
        names = {Path(p.name).relative_to(output.name).as_posix() for p in archive.getmembers()}
    assert {"images/api.tar.gz", "images/postgres.tar.gz", "images/caddy.tar.gz", "server.sh", "SHA256SUMS"} <= names
    subprocess.run([str(output / "server.sh"), "verify"], check=True, capture_output=True)


def fake_docker(tmp_path):
    tools = tmp_path / "tools"; tools.mkdir()
    log = tmp_path / "calls.txt"
    executable = tools / "docker"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$PTCG_TEST_CALLS"\nif [ "$1" = info ]; then echo linux/x86_64; fi\n')
    executable.chmod(0o700)
    return {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"], "PTCG_TEST_CALLS": str(log)}, log


@pytest.mark.parametrize(("mode", "accepted"), [
    ("classic", True), ("containerd", True), ("wrong-content", False), ("wrong-platform", False),
])
def test_load_checks_both_store_identities_and_platform(installer, tmp_path, mode, accepted):
    output, _ = installer
    env, log = fake_docker(tmp_path)
    docker = Path(env["PATH"].split(os.pathsep)[0]) / "docker"
    docker.write_text("""#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$PTCG_TEST_CALLS"
. ./images.env
case "$1" in
    info) echo linux/aarch64 ;;
    load) ;;
    image)
        case "$5" in
            "$API_IMAGE") classic=$API_ID; oci=$API_MANIFEST_ID ;;
            "$POSTGRES_IMAGE") classic=$POSTGRES_ID; oci=$POSTGRES_MANIFEST_ID ;;
            "$CADDY_IMAGE") classic=$CADDY_ID; oci=$CADDY_MANIFEST_ID ;;
            *) exit 1 ;;
        esac
        case "$TEST_STORE" in
            classic) echo "$classic $PLATFORM" ;;
            containerd) echo "$oci $PLATFORM" ;;
            wrong-content) echo "sha256:invalid $PLATFORM" ;;
            wrong-platform) echo "$oci linux/amd64" ;;
        esac ;;
esac
""")
    env["TEST_STORE"] = mode
    result = subprocess.run([str(output / "server.sh"), "load", "--force"], env=env, capture_output=True)
    assert (result.returncode == 0) == accepted, result.stderr
    loads = [line for line in log.read_text().splitlines() if line.startswith("load ")]
    assert loads == [f"load --input images/{name}.tar.gz" for name in
                     (("api", "postgres", "caddy") if accepted else ("api",))]
    if accepted:
        log.unlink()
        subprocess.run([str(output / "server.sh"), "load"], env=env, check=True, capture_output=True)
        assert "load --input" not in log.read_text()
    else:
        assert b"Wrong image ID or architecture: api" in result.stderr and b"; got " in result.stderr


def test_wrong_architecture_and_changed_files_stop_before_loading(installer, tmp_path):
    output, _ = installer; env, log = fake_docker(tmp_path)
    result = subprocess.run([str(output / "server.sh"), "load"], env=env, capture_output=True)
    assert result.returncode and "requires linux/arm64" in result.stderr.decode()
    assert "load" not in log.read_text()
    log.unlink()
    (output / "compose.base.json").write_text("changed")
    assert subprocess.run([str(output / "server.sh"), "load"], env=env, capture_output=True).returncode
    assert not log.exists()


def test_stop_preserves_volumes_even_if_archives_have_been_removed(installer, tmp_path):
    output, _ = installer; env, log = fake_docker(tmp_path)
    (output / "images/api.tar.gz").unlink()
    state = output / "state/deployment"; state.mkdir(parents=True)
    (state / "compose.yaml").write_text("{}")
    subprocess.run([str(output / "server.sh"), "stop"], env=env, check=True, capture_output=True)
    calls = log.read_text()
    assert "down" in calls and "--volumes" not in calls and "prune" not in calls
