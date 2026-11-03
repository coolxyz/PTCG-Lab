"""Configure a fresh offline API installation using the image's Python runtime."""

import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile

if __package__:
    from scripts.packaging import public_api
else:
    import public_api


def setup(package, state, *, host, email, port=443, http_port=80,
          bind="0.0.0.0", subnet="172.30.80.0/28", staging=False):
    public_api.validate(host, email, port, http_port, subnet)
    if bind not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("Invalid bind address")
    package, state = Path(package), Path(state)
    target = state / "deployment"
    if target.exists() or target.is_symlink():
        raise ValueError("Existing deployment retained; setup never overwrites it")
    metadata = json.loads((package / "IMAGES.json").read_bytes())
    base = json.loads((package / "compose.base.json").read_bytes())
    if metadata.get("schema") != 1 or metadata.get("kind") != "ptcg-offline-api":
        raise ValueError("Invalid offline package")
    origin = f"https://{host}" + (f":{port}" if port != 443 else "")
    server_id = "server-" + secrets.token_hex(12)
    for name in ("api", "migrate"):
        base["services"][name]["environment"].update({
            "PTCG_ONLINE_V2_PUBLIC_ORIGIN": origin,
            "PTCG_ONLINE_V2_SERVER_ID": server_id,
        })
    doc = public_api.compose(base, metadata["caddy"]["tag"], host, email,
                            port, http_port, bind, subnet)
    doc["name"] = "ptcg-server-" + secrets.token_hex(8)
    password = secrets.token_urlsafe(36)
    with tempfile.TemporaryDirectory(prefix=".setup-", dir=state) as temporary:
        work = Path(temporary)
        private = work / "private"; private.mkdir(mode=0o700)
        files = {
            "compose.yaml": json.dumps(doc, indent=2) + "\n",
            "Caddyfile": public_api.caddyfile(host, email, port, staging=staging),
            "private/database.env": f"POSTGRES_USER=ptcg_v2\nPOSTGRES_DB=ptcg_v2\nPOSTGRES_PASSWORD={password}\n",
            "private/api.env": f"PTCG_ONLINE_V2_DATABASE_URL=postgresql://ptcg_v2:{password}@database:5432/ptcg_v2\n",
            "client.json": json.dumps({"origin": origin, "caFile": None}) + "\n",
            "origin.txt": origin + "\n",
            "DEPLOYMENT.json": json.dumps({
                "schema": 1, "project": doc["name"], "serverId": server_id,
                "origin": origin, "platform": metadata["platform"],
                "ruleReleaseId": metadata["ruleReleaseId"], "acmeStaging": staging,
            }, indent=2) + "\n",
        }
        for name, text in files.items():
            path = work / name
            with path.open("x") as stream:
                os.chmod(path, 0o644 if name == "Caddyfile" else 0o600)
                stream.write(text)
        work.rename(target)
    return origin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--state", type=Path, default=Path("/state"))
    parser.add_argument("--host", required=True, help="Public API DNS name")
    parser.add_argument("--email", required=True, help="ACME contact email")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--http-port", type=int, default=80)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--proxy-subnet", default="172.30.80.0/28")
    parser.add_argument("--staging", action="store_true")
    args = parser.parse_args()
    print("Configured " + setup(args.package, args.state, host=args.host,
          email=args.email, port=args.port, http_port=args.http_port,
          bind=args.bind, subnet=args.proxy_subnet, staging=args.staging))


if __name__ == "__main__":
    main()
