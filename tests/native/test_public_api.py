from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.packaging import api_server, configure_macos, public_api, server_backup
from ptcg_desktop_api.configure import configure
from ptcg_online_client.contracts import ClientError


def base():
    return api_server.compose_document("api", "pg", "linux/arm64", "https://battle.example.com", "server", "rules-current", "a" * 64, 443, "0.0.0.0")


@pytest.mark.parametrize("host,email,subnet", [
    ("127.0.0.1", "ops@example.com", "172.30.80.0/28"),
    ("*.example.com", "ops@example.com", "172.30.80.0/28"),
    ("https://example.com", "ops@example.com", "172.30.80.0/28"),
    ("battle.example.com\nadmin off", "ops@example.com", "172.30.80.0/28"),
    ("battle.example.com", "ops@example.com\n}", "172.30.80.0/28"),
    ("battle.example.com", "ops@example.com", "0.0.0.0/0"),
    ("battle.example.com", "ops@example.com", "8.8.8.0/24"),
    ("battle.example.com", "ops@example.com", "127.0.0.0/28"),
    ("battle.example.com", "ops@example.com", "169.254.0.0/28"),
])
def test_acme_inputs_reject_injection_and_unbounded_proxy_trust(host, email, subnet):
    with pytest.raises(ValueError):
        public_api.validate(host, email, 443, 80, subnet)


def test_public_gateway_preserves_host_auth_websocket_and_ip_boundaries():
    doc = public_api.compose(base(), "caddy-pinned", "battle.example.com", "ops@example.com")
    s = doc["services"]
    assert "ports" not in s["api"] and "ports" not in s["database"]
    assert s["api"]["read_only"] and s["api"]["cap_drop"] == ["ALL"]
    command = s["api"]["entrypoint"]
    assert "--ssl-certfile" not in command and "--proxy-headers" in command
    assert command[command.index("--forwarded-allow-ips") + 1] == "172.30.80.14"
    assert doc["networks"]["proxy"]["internal"] and doc["networks"]["backend"]["internal"]
    assert s["caddy"]["networks"]["proxy"]["ipv4_address"] == "172.30.80.14"
    # Docker's allocator must not give the API the reserved gateway address first.
    import ipaddress
    assert ipaddress.ip_address("172.30.80.14") not in ipaddress.ip_network(doc["networks"]["proxy"]["ipam"]["config"][0]["ip_range"])
    assert "edge" not in s["api"]["networks"]
    assert s["caddy"]["ports"] == ["0.0.0.0:80:80", "0.0.0.0:443:443"]
    assert all(service["logging"]["options"]["max-size"] == "10m" for service in s.values())
    config = public_api.caddyfile("battle.example.com", "ops@example.com")
    assert "X-Forwarded-For {remote_host}" in config and "header_up Host battle.example.com" in config
    assert "header_up -Forwarded" in config
    assert "Authorization" not in config and "Cookie" not in config and "Origin" not in config
    assert "stream_timeout" not in config and "tls_insecure" not in config
    assert public_api.PRODUCTION_CA in config
    assert public_api.STAGING_CA in public_api.caddyfile("battle.example.com", "ops@example.com", staging=True)
    high_port = public_api.caddyfile("battle.example.com", "ops@example.com", 28443)
    assert "https://battle.example.com {" in high_port and "header_up Host battle.example.com:28443" in high_port
    assert "redir https://battle.example.com:28443{uri}" in high_port


@pytest.mark.parametrize("helper", [configure_macos.configure, configure])
def test_acme_client_configuration_uses_system_roots_without_a_local_ca(tmp_path, helper):
    kit = tmp_path / "kit"; kit.mkdir()
    raw = json.dumps({"origin":"https://battle.example.com", "caFile":None}).encode()
    (kit / "client.json").write_bytes(raw)
    (kit / "MANIFEST.json").write_text(json.dumps({"kind":"ptcg-api-compose-preview", "files":{"client.json":sha256(raw).hexdigest()}}))
    state = tmp_path.resolve() / "state"
    helper(kit, state)
    config = json.loads((state / "connection.json").read_bytes())
    assert config["servers"]["preview"] == {"origin":"https://battle.example.com"}
    assert not (state / "preview-server-ca.pem").exists()
    assert not (state / "credentials").exists()
    before = (state / "connection.json").read_bytes()
    with pytest.raises((ValueError, ClientError)):
        helper(kit, state)
    assert (state / "connection.json").read_bytes() == before


def backup_fixture(tmp_path):
    root = tmp_path / "backup";root.mkdir()
    data=b"synthetic dump bytes";(root / "database.dump").write_bytes(data)
    report={"project":"ptcg-preview-test", "origin":"https://battle.example.com", "serverId":"server", "ruleReleaseId":"current"}
    value={"schema":1,"kind":"ptcg-private-database-backup","postgresMajor":17,"origin":report["origin"],"serverId":"server","ruleReleaseId":"current","dumpSha256":sha256(data).hexdigest()}
    (root / "BACKUP.json").write_text(json.dumps(value))
    return root,report,sha256((root / "BACKUP.json").read_bytes()).hexdigest()


def test_backup_hash_and_deployment_identity_are_required(tmp_path):
    root,report,pin=backup_fixture(tmp_path)
    server_backup.verify_backup(root,report,pin)
    for changes in ({"serverId":"other"},{"origin":"https://other.example.com"},{"ruleReleaseId":"old"}):
        with pytest.raises(ValueError):server_backup.verify_backup(root,{**report,**changes},pin)
    (root / "database.dump").write_bytes(b"tampered")
    with pytest.raises(ValueError,match="checksum"):
        server_backup.verify_backup(root,report,pin)


@pytest.mark.parametrize("running,objects", [(True,"0"),(False,"1")])
def test_restore_never_overwrites_a_running_or_populated_database(tmp_path,monkeypatch,running,objects):
    root,report,pin=backup_fixture(tmp_path);calls=[]
    def fake_run(*args,**kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=b"running-api" if args[2]=="ps" and running else b"",returncode=0)
    monkeypatch.setattr(server_backup,"run",fake_run)
    monkeypatch.setattr(server_backup,"scalar",lambda *args:objects)
    with pytest.raises(ValueError):server_backup.restore(tmp_path,report,root,pin)
    assert not any("pg_restore" in args for args in calls)


def test_restore_is_single_transaction_and_does_not_start_api(tmp_path,monkeypatch):
    root,report,pin=backup_fixture(tmp_path);calls=[];scalars=iter(["0","server"])
    monkeypatch.setattr(server_backup,"scalar",lambda *args:next(scalars))
    def fake_run(*args,**kwargs):
        calls.append(args);return SimpleNamespace(stdout=b"",returncode=0)
    monkeypatch.setattr(server_backup,"run",fake_run)
    server_backup.restore(tmp_path,report,root,pin)
    restore=next(args for args in calls if "pg_restore" in args)
    assert "--single-transaction" in restore and "--no-owner" in restore
    assert all(args[-1]=="database" for args in calls if args[2]=="up")
