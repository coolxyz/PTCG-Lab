from pathlib import Path
import runpy

import pytest


CONFIG = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/packaging/api-update-config.py")
)
check_config = CONFIG["check_config"]
RULE_DIRECTORY = CONFIG["RULE_DIRECTORY"]
RULE_RELEASE = CONFIG["RULE_RELEASE"]
RULE_PIN = CONFIG["RULE_PIN"]
UPDATE = ("new-image", "linux/amd64", "old-rule", "old-pin", "new-rule", "new-pin")


def deployment():
    environment = {RULE_DIRECTORY: "/opt/ptcg-rules", RULE_RELEASE: "old-rule", RULE_PIN: "old-pin",
                   "PTCG_ONLINE_V2_SERVER_ID": "existing-server"}
    return {
        "name": "existing-project",
        "services": {
            "api": {"image": "old-image", "environment": environment.copy(), "ports": ["8784:8784"]},
            "migrate": {"image": "old-image", "environment": environment.copy(),
                        "entrypoint": ["python", "-m", "ptcg_online_server.migrate"]},
            "database": {"image": "postgres:17"},
        },
        "volumes": {"database": {}},
    }


def test_only_api_and_migration_identities_change():
    before = deployment()
    after, previous = check_config(before, *UPDATE)
    assert previous == "old-image"
    assert after["services"]["api"]["image"] == after["services"]["migrate"]["image"] == "new-image"
    assert after["services"]["api"]["environment"][RULE_RELEASE] == "new-rule"
    assert after["services"]["migrate"]["environment"][RULE_PIN] == "new-pin"
    assert after["services"]["api"]["ports"] == before["services"]["api"]["ports"]
    assert after["services"]["api"]["environment"]["PTCG_ONLINE_V2_SERVER_ID"] == "existing-server"
    assert after["services"]["database"] == before["services"]["database"]
    assert after["name"] == before["name"] and after["volumes"] == before["volumes"]
    assert check_config(after, *UPDATE)[0] == after


@pytest.mark.parametrize("target", ["/opt", "/opt/ptcg-rules", "/opt/ptcg-rules/bundle.json"])
def test_rejects_a_mount_that_would_shadow_new_rules(target):
    before = deployment()
    before["services"]["api"]["volumes"] = [{"type": "bind", "source": "/old", "target": target}]
    with pytest.raises(ValueError, match="shadows"):
        check_config(before, *UPDATE)


def test_refuses_unrelated_rule_release():
    before = deployment()
    before["services"]["api"]["environment"][RULE_RELEASE] = "another-rule"
    with pytest.raises(ValueError, match="supported upgrade path"):
        check_config(before, *UPDATE)


def test_offline_launcher_inventory_can_reload_new_archive(tmp_path):
    from hashlib import sha256
    import json
    import shutil

    kit = tmp_path / "kit"
    (kit / "images").mkdir(parents=True)
    for name in CONFIG["KIT_FILES"]:
        (kit / name).write_bytes(b"unchanged fixture")
    metadata = {
        "schema": 1, "kind": "ptcg-offline-api", "platform": "linux/amd64",
        "ruleReleaseId": "old-rule", "ruleManifestSha256": "old-pin",
        "api": {"tag": "old-image", "id": "old-id", "manifestId": "old-manifest"},
        "postgres": {"tag": "postgres:17", "id": "db-id", "manifestId": "db-manifest"},
        "caddy": {"tag": "caddy:2", "id": "edge-id", "manifestId": "edge-manifest"},
    }
    (kit / "compose.base.json").write_text(json.dumps(deployment()))
    (kit / "IMAGES.json").write_text(json.dumps(metadata))
    old_hashes = {name: sha256((kit / name).read_bytes()).hexdigest() for name in CONFIG["KIT_FILES"]}
    old_manifest = {"kind": "ptcg-offline-api", "ruleReleaseId": "old-rule", "files": old_hashes,
                    "images": {"old-image": "old-id", "postgres:17": "db-id", "caddy:2": "edge-id"}}
    (kit / "MANIFEST.json").write_text(json.dumps(old_manifest))
    checksums = {**old_hashes, "MANIFEST.json": sha256((kit / "MANIFEST.json").read_bytes()).hexdigest()}
    (kit / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in checksums.items()))
    new_archive = b"approved new image archive"
    update = {
        "platform": "linux/amd64", "ruleReleaseId": "new-rule", "ruleManifestSha256": "new-pin",
        "image": {"tag": "new-image", "id": "new-id", "manifestId": "new-manifest"},
        "imageArchiveSha256": sha256(new_archive).hexdigest(), "upstreamCommit": "approved",
    }
    stage = tmp_path / "stage"
    CONFIG["kit_update"](kit, stage, update, "old-rule", "old-pin", old_hashes)
    assert "API_IMAGE=new-image\n" in (stage / "images.env").read_text()
    for file in stage.iterdir():
        shutil.copyfile(file, kit / file.name)
    (kit / "images/api.tar.gz").write_bytes(new_archive)
    assert (kit / "server.sh").read_bytes() == b"unchanged fixture"
    assert json.loads((kit / "MANIFEST.json").read_text())["images"]["caddy:2"] == "edge-id"
    new_hashes = {name: sha256((kit / name).read_bytes()).hexdigest() for name in CONFIG["KIT_FILES"]}
    CONFIG["kit_update"](kit, tmp_path / "second-stage", update, "old-rule", "old-pin", new_hashes)
    assert not (tmp_path / "second-stage").exists()
