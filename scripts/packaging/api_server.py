"""Package one current-rule API image, offline image archives and fresh Compose state."""

import argparse
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import ipaddress
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.packaging.common import manifest, new_output, record, run, write  # noqa: E402


def public_host(host, port, bind):
    if not 1024 <= port <= 65535 or port in {8765, 8770, 8443, 8874}:
        raise ValueError("Choose a separate test port (default 8876)")
    if bind not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("Bind must be 127.0.0.1 or 0.0.0.0")
    try:
        ip = ipaddress.ip_address(host)
        if ip.version != 4 or ip.is_unspecified or ip.is_multicast:
            raise ValueError("Use a concrete IPv4 address or DNS name")
        return "IP:" + host
    except ValueError:
        if (
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host)
            or host.replace(".", "").isdigit()
        ):
            raise ValueError("Invalid public host") from None
        return "DNS:" + host


def copy_rules(directory, pin, target):
    # Only validated manifest components; no directory copy, databases or images.
    sys.path.insert(0, str(ROOT / "packages/engine-provider/src"))
    from ptcg_engine_provider.bundle import DirectoryEngineProvider

    bundle = DirectoryEngineProvider(Path(directory), pin).load()
    raw = (Path(directory) / "bundle.json").read_bytes()
    if sha256(raw).hexdigest() != pin:
        raise ValueError("Rule manifest changed")
    target.mkdir()
    (target / "bundle.json").write_bytes(raw)
    for component in json.loads(raw)["components"]:
        path = target / component["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bundle.assets[component["name"]].content)
    # Non-root image user must be able to read installed immutable inputs.
    for path in target.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    return bundle.bundle_id


def tls(output, host, san):
    private = output / "private"
    private.mkdir(mode=0o700)
    directory = output / "tls"
    directory.mkdir(mode=0o755)
    # The enclosing output is 0700. The mounted TLS directory must be readable
    # by container UID 10001 without changing host ownership or using root API.
    with tempfile.TemporaryDirectory(prefix="ptcg-test-tls-") as scratch:
        scratch = Path(scratch)
        config = scratch / "openssl.cnf"
        config.write_text(
            "[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=PTCG Lab Test CA\n[v3_ca]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n[v3_server]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\nsubjectAltName="
            + san
            + ",IP:127.0.0.1,DNS:localhost\n"
        )
        run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "30",
                "-config",
                config,
                "-extensions",
                "v3_ca",
                "-keyout",
                private / "ca-key.pem",
                "-out",
                directory / "ca.pem",
            ]
        )
        run(
            [
                "openssl",
                "req",
                "-new",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                "/CN=" + host,
                "-keyout",
                directory / "server-key.pem",
                "-out",
                scratch / "server.csr",
            ]
        )
        run(
            [
                "openssl",
                "x509",
                "-req",
                "-days",
                "30",
                "-in",
                scratch / "server.csr",
                "-CA",
                directory / "ca.pem",
                "-CAkey",
                private / "ca-key.pem",
                "-set_serial",
                str(secrets.randbits(128)),
                "-extfile",
                config,
                "-extensions",
                "v3_server",
                "-out",
                directory / "server.pem",
            ]
        )
    (private / "ca-key.pem").chmod(0o600)
    for file in directory.iterdir():
        file.chmod(0o644)


def image_id(image):
    info = json.loads(run(["docker", "image", "inspect", image], capture=True))[0]
    return info["Id"], info["Os"] + "/" + info["Architecture"]


def verify_api_sources(image):
    modules = {
        "ptcg_online_server": "apps/online-server/src/ptcg_online_server",
        "ptcg_core": "packages/battle-core/src/ptcg_core",
        "ptcg_protocol": "packages/battle-protocol/src/ptcg_protocol",
        "ptcg_engine_provider": "packages/engine-provider/src/ptcg_engine_provider",
    }
    expected = {
        module: {
            p.relative_to(ROOT / folder).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / folder).rglob("*.py")
            if "__pycache__" not in p.parts
        }
        for module, folder in modules.items()
    }
    code = (
        "import json; from ptcg_core.identity import source_hashes; print(json.dumps({n:source_hashes(n) for n in "
        + repr(list(modules))
        + "}))"
    )
    actual = json.loads(
        run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--entrypoint",
                "python",
                image,
                "-c",
                code,
            ],
            capture=True,
        )
    )
    if actual != expected:
        raise ValueError(
            "API image sources differ from current checkout; build the current image"
        )


def save_image(image, output):
    with gzip.open(output, "wb", compresslevel=5) as archive:
        with subprocess.Popen(
            ["docker", "save", image], stdout=subprocess.PIPE
        ) as process:
            shutil.copyfileobj(process.stdout, archive)
            process.stdout.close()
            if process.wait() != 0:
                raise RuntimeError("Image export failed")


def compose_document(
    api, postgres, platform, origin, server_id, release, pin, port, bind
):
    environment = {
        "PTCG_ONLINE_V2_SERVER_ID": server_id,
        "PTCG_ONLINE_V2_PUBLIC_ORIGIN": origin,
        "PTCG_ONLINE_V2_RULE_DIRECTORY": "/opt/ptcg-rules",
        "PTCG_ONLINE_V2_RULE_MANIFEST_SHA256": pin,
        "PTCG_ONLINE_V2_APPROVED_RULE_RELEASE_ID": release,
        "PTCG_ONLINE_V2_WORKER_RSS_MB": "384",
    }
    common = {
        "image": api,
        "platform": platform,
        "pull_policy": "never",
        "env_file": ["./private/api.env"],
        "environment": environment,
        "read_only": True,
        "tmpfs": ["/tmp"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "cpus": 1.0,
        "mem_limit": "768m",
    }
    health = "import os,ssl,urllib.request; u=os.environ['PTCG_ONLINE_V2_PUBLIC_ORIGIN']; r=urllib.request.Request('https://127.0.0.1:8784/readyz',headers={'Host':u.split('://',1)[1]}); urllib.request.urlopen(r,context=ssl.create_default_context(cafile='/run/tls/ca.pem'),timeout=4)"
    return {
        "services": {
            "database": {
                "image": postgres,
                "platform": platform,
                "pull_policy": "never",
                "env_file": ["./private/database.env"],
                "restart": "unless-stopped",
                "volumes": ["database:/var/lib/postgresql/data"],
                "cpus": 0.5,
                "mem_limit": "256m",
                "healthcheck": {
                    # The image's temporary initialization server accepts Unix
                    # sockets before initialization finishes. Wait for final TCP.
                    "test": ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U ptcg_v2 -d ptcg_v2"],
                    "interval": "3s",
                    "timeout": "3s",
                    "retries": 30,
                },
            },
            "migrate": {
                **common,
                "entrypoint": ["python", "-m", "ptcg_online_server.migrate"],
                "restart": "no",
                "depends_on": {"database": {"condition": "service_healthy"}},
            },
            "api": {
                **common,
                "restart": "unless-stopped",
                "stop_grace_period": "60s",
                "entrypoint": [
                    "python",
                    "-m",
                    "uvicorn",
                    "ptcg_online_server.api:create_app",
                    "--factory",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8784",
                    "--ssl-certfile",
                    "/run/tls/server.pem",
                    "--ssl-keyfile",
                    "/run/tls/server-key.pem",
                    "--no-proxy-headers",
                    "--no-access-log",
                    "--no-server-header",
                    "--limit-concurrency",
                    "72",
                    "--timeout-keep-alive",
                    "5",
                    "--ws-max-size",
                    "16384",
                    "--ws-max-queue",
                    "2",
                    "--ws-per-message-deflate",
                    "false",
                ],
                "ports": [f"{bind}:{port}:8784"],
                "volumes": [
                    {
                        "type": "bind",
                        "source": "./tls",
                        "target": "/run/tls",
                        "read_only": True,
                        "bind": {"create_host_path": False},
                    }
                ],
                "depends_on": {
                    "migrate": {"condition": "service_completed_successfully"}
                },
                "healthcheck": {
                    "test": ["CMD", "python", "-c", health],
                    "interval": "5s",
                    "timeout": "5s",
                    "start_period": "15s",
                    "retries": 20,
                },
            },
        },
        "volumes": {"database": {}},
    }


def build(
    directory,
    pin,
    output,
    *,
    host="localhost",
    port=None,
    bind="127.0.0.1",
    platform="linux/arm64",
    base_image=None,
    postgres_image="postgres:17",
    tls_mode="local-ca",
    email=None,
    http_port=80,
    caddy_image="caddy:2",
    acme_staging=False,
    proxy_subnet="172.30.80.0/28",
    server_id=None,
):
    if tls_mode not in {"local-ca", "acme"}:
        raise ValueError("Choose local-ca or acme TLS")
    port = port if port is not None else (443 if tls_mode == "acme" else 8876)
    if tls_mode == "acme":
        from scripts.packaging import public_api
        public_api.validate(host, email, port, http_port, proxy_subnet)
        if bind not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("Invalid public bind")
    else:
        san = public_host(host, port, bind)
    if server_id is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", server_id):
        raise ValueError("Invalid deployment server ID")
    if platform not in {"linux/arm64", "linux/amd64"}:
        raise ValueError("Choose linux/arm64 or linux/amd64")
    output = new_output(output)
    token = (
        datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)
    )
    if base_image is None:
        base_image = "ptcg-lab-api-base:" + token
        run(
            [
                "docker",
                "build",
                "--platform",
                platform,
                "-f",
                "ops/online/Dockerfile",
                "-t",
                base_image,
                ".",
            ]
        )
    if image_id(base_image)[1] != platform:
        raise ValueError("Base image architecture mismatch")
    verify_api_sources(base_image)
    with tempfile.TemporaryDirectory(prefix="ptcg-rules-image-") as scratch:
        scratch = Path(scratch)
        bundle = copy_rules(directory, pin, scratch / "rules")
        write(
            scratch / "Dockerfile",
            "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nCOPY --chown=10001:10001 rules /opt/ptcg-rules\nENV PTCG_ONLINE_V2_RULE_DIRECTORY=/opt/ptcg-rules\n",
        )
        api = "ptcg-lab-api-preview:" + token
        run(
            [
                "docker",
                "build",
                "--platform",
                platform,
                "--build-arg",
                "BASE_IMAGE=" + base_image,
                "-t",
                api,
                scratch,
            ]
        )
    identity_code = (
        "import json; from ptcg_engine_provider.bundle import DirectoryEngineProvider; from ptcg_core.factory import CoreFactory; b=DirectoryEngineProvider('/opt/ptcg-rules',"
        + repr(pin)
        + ").load(); f=CoreFactory(b); print(json.dumps({'release':f.runtime.release_id}))"
    )
    release = json.loads(
        run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--entrypoint",
                "python",
                api,
                "-c",
                identity_code,
            ],
            capture=True,
        )
    )["release"]
    # Pin the platform-specific PostgreSQL manifest, then export a private local
    # tag so docker load works without a registry or RepoDigest lookup.
    try:
        pg_info = json.loads(
            run(["docker", "image", "inspect", postgres_image], capture=True)
        )[0]
    except subprocess.CalledProcessError:
        run(["docker", "pull", "--platform", platform, postgres_image])
        pg_info = json.loads(
            run(["docker", "image", "inspect", postgres_image], capture=True)
        )[0]
    if pg_info["Os"] + "/" + pg_info["Architecture"] != platform:
        raise ValueError("PostgreSQL image architecture mismatch")
    pg_version = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "postgres",
            postgres_image,
            "--version",
        ],
        capture=True,
    ).strip()
    if not re.fullmatch(r"postgres \(PostgreSQL\) 17\.[0-9]+(?: .*)?", pg_version):
        raise ValueError("This preview requires PostgreSQL 17")
    postgres = "ptcg-lab-postgres-preview:" + pg_info["Id"].split(":")[1][:16]
    run(["docker", "tag", pg_info["Id"], postgres])
    origin = f"https://{host}" if port == 443 else f"https://{host}:{port}"
    project = "ptcg-preview-" + token
    if tls_mode == "local-ca":
        tls(output, host, san)
    else:
        (output / "private").mkdir(mode=0o700)
    password = secrets.token_urlsafe(36)
    write(
        output / "private/database.env",
        f"POSTGRES_USER=ptcg_v2\nPOSTGRES_DB=ptcg_v2\nPOSTGRES_PASSWORD={password}\n",
    )
    write(
        output / "private/api.env",
        f"PTCG_ONLINE_V2_DATABASE_URL=postgresql://ptcg_v2:{password}@database:5432/ptcg_v2\n",
    )
    document = compose_document(
        api,
        postgres,
        platform,
        origin,
        server_id or "server-" + secrets.token_hex(12),
        release,
        pin,
        port,
        bind,
    )
    extra_images = {}
    if tls_mode == "acme":
        try:
            edge_id, edge_platform = image_id(caddy_image)
        except subprocess.CalledProcessError:
            run(["docker", "pull", "--platform", platform, caddy_image])
            edge_id, edge_platform = image_id(caddy_image)
        if edge_platform != platform:
            raise ValueError("Caddy image architecture mismatch")
        edge_tag = "ptcg-lab-caddy:" + edge_id.split(":")[1][:16]
        run(["docker", "tag", edge_id, edge_tag])
        document = public_api.compose(document, edge_tag, host, email, port, http_port, bind, proxy_subnet)
        write(output / "Caddyfile", public_api.caddyfile(host, email, port, staging=acme_staging), mode=0o644)
        extra_images[edge_tag] = edge_id
    document["name"] = project
    record(output / "compose.yaml", document)
    shutil.copyfile(ROOT / "scripts/packaging/manage_server.py", output / "manage.py")
    shutil.copyfile(ROOT / "scripts/packaging/server_backup.py", output / "server_backup.py")
    shutil.copyfile(
        ROOT / "scripts/packaging/configure_macos.py", output / "configure-mac.py"
    )
    record(output / "client.json", {"origin": origin, "caFile": "tls/ca.pem" if tls_mode == "local-ca" else None})
    write(
        output / "configure-mac.command",
        '#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\nPTCG_APP="${PTCG_APP:-../macos-arm64/PTCG Lab Preview.app}"\nexec "$PTCG_APP/Contents/Resources/backend/python/bin/python3" -I -B configure-mac.py\n',
        mode=0o700,
    )
    images = output / "images"
    images.mkdir()
    save_image(api, images / "api.tar.gz")
    save_image(postgres, images / "postgres.tar.gz")
    if extra_images:
        save_image(edge_tag, images / "caddy.tar.gz")
    shutil.copyfile(ROOT / "scripts/packaging/server-README.md", output / "README.md")
    return manifest(
        output,
        "ptcg-api-compose-preview",
        platform=platform,
        project=project,
        origin=origin,
        ruleReleaseId=release,
        ruleBundleId=bundle,
        ruleManifestSha256=pin,
        postgresUpstream=pg_info["RepoDigests"],
        postgresVersion=pg_version,
        images={api: image_id(api)[0], postgres: image_id(postgres)[0], **extra_images},
        tlsMode=tls_mode,
        acmeStaging=acme_staging if tls_mode == "acme" else False,
        serverId=document["services"]["api"]["environment"]["PTCG_ONLINE_V2_SERVER_ID"],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--rule-pin", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "release/api-server")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, help="HTTPS port: local-ca 8876, acme 443")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--tls-mode", choices=("local-ca", "acme"), default="local-ca")
    parser.add_argument("--email", help="ACME contact email for a public DNS domain")
    parser.add_argument("--http-port", type=int, default=80)
    parser.add_argument("--caddy-image", default="caddy:2")
    parser.add_argument("--acme-staging", action="store_true", help="Use untrusted Let's Encrypt staging certificates")
    parser.add_argument("--proxy-subnet", default="172.30.80.0/28", help="Unused private subnet for the isolated trusted edge network")
    parser.add_argument("--server-id", help="Explicit original server ID when preparing a disaster-restore kit")
    parser.add_argument(
        "--platform", choices=("linux/arm64", "linux/amd64"), default="linux/arm64"
    )
    parser.add_argument(
        "--base-image",
        help="Explicit already-verified API base image; default builds current sources",
    )
    parser.add_argument(
        "--postgres-image",
        default="postgres:17",
        help="Approved PostgreSQL 17 tag or digest; reuse a matching local image before pulling",
    )
    args = parser.parse_args()
    result = build(
        args.rules,
        args.rule_pin,
        args.output,
        host=args.host,
        port=args.port,
        bind=args.bind,
        platform=args.platform,
        base_image=args.base_image,
        postgres_image=args.postgres_image,
        tls_mode=args.tls_mode,
        email=args.email,
        http_port=args.http_port,
        caddy_image=args.caddy_image,
        acme_staging=args.acme_staging,
        proxy_subnet=args.proxy_subnet,
        server_id=args.server_id,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "origin": result["origin"],
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
