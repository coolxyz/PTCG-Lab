"""Preview packaging must isolate secrets, rules and existing deployment state."""

from hashlib import sha256
import json
import socket
import ssl
from threading import Thread
from types import SimpleNamespace

import pytest
from scripts.packaging import api_server, common, configure_macos, manage_server
from scripts.public.policy import allowed_source, inspect, source_files


def test_generated_test_ca_passes_strict_tls_verification(tmp_path):
    output = tmp_path / "strict-tls"
    output.mkdir(mode=0o700)
    api_server.tls(output, "localhost", "DNS:localhost")
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(
        output / "tls/server.pem", output / "tls/server-key.pem"
    )
    client_context = ssl.create_default_context(cafile=str(output / "tls/ca.pem"))
    client_context.verify_flags |= ssl.VERIFY_X509_STRICT
    errors = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(5)

        def serve():
            try:
                peer, _ = listener.accept()
                with (
                    peer,
                    server_context.wrap_socket(peer, server_side=True) as connection,
                ):
                    connection.sendall(b"strict-tls-ok")
            except Exception as error:
                errors.append(error)

        worker = Thread(target=serve, daemon=True)
        worker.start()
        try:
            with socket.create_connection(listener.getsockname(), timeout=5) as peer:
                with client_context.wrap_socket(
                    peer, server_hostname="localhost"
                ) as connection:
                    assert connection.recv(100) == b"strict-tls-ok"
        finally:
            worker.join(timeout=6)
        assert not errors and not worker.is_alive()


def test_release_products_never_enter_public_source(tmp_path):
    assert not allowed_source(".") and not allowed_source("")
    for name in (
        "release/README.md",
        "release/api-server/compose.yaml",
        "release/private.txt",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic-private")
        assert not allowed_source(name)
        assert "private_or_generated_path" in inspect(name, b"synthetic")
    script = tmp_path / "scripts/packaging/api_server.py"
    script.parent.mkdir(parents=True)
    script.write_text("# public build script")
    assert source_files(tmp_path) == ["scripts/packaging/api_server.py"]


def test_output_refuses_overwrite_and_symlink(tmp_path):
    output = common.new_output(tmp_path / "candidate")
    assert output.stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        common.new_output(output)
    link = tmp_path / "linked"
    link.symlink_to(output, target_is_directory=True)
    with pytest.raises(ValueError):
        common.new_output(link / "new")
    assert not (output / "new").exists()


def test_source_archive_without_git_remains_content_traceable(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "ROOT", tmp_path)
    source = tmp_path / "README.md"
    source.write_text("original source archive")
    first = common.provenance()
    assert first["commit"] is None and first["dirty"] is None
    source.write_text("changed source archive")
    assert common.provenance()["publicSourceSha256"] != first["publicSourceSha256"]


@pytest.mark.parametrize(
    "host,port,bind",
    [
        ("127.0.0.1", 8874, "127.0.0.1"),
        ("bad;host", 8876, "127.0.0.1"),
        ("0.0.0.0", 8876, "0.0.0.0"),
        ("localhost", 8876, "10.0.0.1"),
    ],
)
def test_unsafe_or_existing_lan_port_is_rejected(host, port, bind):
    with pytest.raises(ValueError):
        api_server.public_host(host, port, bind)


def test_only_current_image_sources_are_accepted(monkeypatch):
    monkeypatch.setattr(
        api_server, "run", lambda *args, **kwargs: json.dumps({"oldCore": "synthetic"})
    )
    with pytest.raises(ValueError, match="sources differ"):
        api_server.verify_api_sources("synthetic-image")


def test_rules_copy_uses_only_verified_components(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    raw = json.dumps({"components": [{"name": "engine", "path": "engine.py"}]}).encode()
    (root / "bundle.json").write_bytes(raw)
    (root / "user.sqlite").write_text("private sentinel")
    verified = SimpleNamespace(
        bundle_id="synthetic",
        assets={"engine": SimpleNamespace(content=b"# verified source")},
    )
    from ptcg_engine_provider import bundle

    monkeypatch.setattr(bundle.DirectoryEngineProvider, "load", lambda self: verified)
    target = tmp_path / "output"
    api_server.copy_rules(root, sha256(raw).hexdigest(), target)
    assert sorted(p.name for p in target.iterdir()) == ["bundle.json", "engine.py"]
    assert (target / "engine.py").read_bytes() == b"# verified source"
    with pytest.raises(ValueError, match="manifest changed"):
        api_server.copy_rules(root, "0" * 64, tmp_path / "bad")


def test_compose_keeps_database_private_and_requires_migration():
    doc = api_server.compose_document(
        "api-tag",
        "db-tag",
        "linux/arm64",
        "https://localhost:8876",
        "synthetic",
        "rules-current",
        "a" * 64,
        8876,
        "127.0.0.1",
    )
    services = doc["services"]
    assert "ports" not in services["database"]
    assert (
        services["api"]["depends_on"]["migrate"]["condition"]
        == "service_completed_successfully"
    )
    assert services["api"]["read_only"] is True
    assert services["api"]["pull_policy"] == "never"
    assert not any("PREVIOUS" in key for key in services["api"]["environment"])


def test_manager_refuses_tamper_missing_images_or_unsafe_paths(tmp_path, monkeypatch):
    path = tmp_path / "compose.yaml"
    path.write_text("synthetic")
    report = {
        "schema": 1,
        "kind": "ptcg-api-compose-preview",
        "project": "ptcg-preview-synthetic",
        "files": {"compose.yaml": sha256(path.read_bytes()).hexdigest()},
    }
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text(json.dumps(report))
    assert manage_server.verify(tmp_path) == report
    path.write_text("changed")
    with pytest.raises(ValueError, match="Changed kit file"):
        manage_server.verify(tmp_path)
    report["files"] = {"../escape": "0" * 64}
    manifest.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        manage_server.verify(tmp_path)
    monkeypatch.setattr(
        manage_server.subprocess,
        "check_output",
        lambda *args, **kwargs: json.dumps(
            [{"Id": "wrong", "Os": "linux", "Architecture": "arm64"}]
        ),
    )
    with pytest.raises(ValueError, match="identity"):
        manage_server.images_match(
            {"images": {"tag": "expected"}, "platform": "linux/arm64"}
        )


def test_configure_mac_never_overwrites_existing_identity_profile(tmp_path):
    state = (tmp_path / "profile").resolve()
    state.mkdir(mode=0o700)
    (state / "connection.json").write_text("existing-private-configuration")
    source = tmp_path / "kit"
    (source / "tls").mkdir(parents=True)
    (source / "client.json").write_text(
        json.dumps({"origin": "https://example.test", "caFile": "tls/ca.pem"})
    )
    (source / "tls/ca.pem").write_text("synthetic-public-ca")
    with pytest.raises(ValueError, match="Existing configuration retained"):
        configure_macos.configure(source, state)
    assert (state / "connection.json").read_text() == "existing-private-configuration"
    assert not (state / "preview-server-ca.pem").exists()
