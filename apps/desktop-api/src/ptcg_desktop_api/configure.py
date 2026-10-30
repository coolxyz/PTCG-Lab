"""Explicit Windows preview server setup. No HTTP endpoint or automatic import."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

from ptcg_online_client.contracts import ClientError
from ptcg_online_client.transport import Server
from .native import StateLock
from .sealed_file import access


def configure(kit, state):
    kit, state = Path(kit).resolve(), Path(state).absolute()
    manifest = json.loads((kit / "MANIFEST.json").read_text(encoding="utf-8"))
    if manifest.get("kind") != "ptcg-api-compose-preview":
        raise ClientError("INVALID_SERVER_KIT")
    values = {}
    for name in ["client.json", "tls/ca.pem"]:
        path = kit / name
        if (
            any(p.is_symlink() or p.is_junction() for p in [path, path.parent])
            or not path.is_file()
            or path.stat().st_size > 65536
        ):
            raise ClientError("INVALID_SERVER_KIT")
        value = path.read_bytes()
        if sha256(value).hexdigest() != manifest.get("files", {}).get(name):
            raise ClientError("INVALID_SERVER_KIT")
        values[name] = value
    connection = json.loads(values["client.json"])
    if (
        set(connection) != {"origin", "caFile"}
        or connection["caFile"] != "tls/ca.pem"
        or b"PRIVATE KEY" in values["tls/ca.pem"]
    ):
        raise ClientError("INVALID_SERVER_KIT")
    Server(connection["origin"], ca_file=str(kit / "tls/ca.pem"))
    lock = StateLock(state)
    try:
        target, ca = state / "connection.json", state / "preview-server-ca.pem"
        if any(p.exists() or p.is_symlink() for p in [target, ca]):
            raise ClientError("EXISTING_CONFIGURATION")
        access(ca, data=values["tls/ca.pem"], limit=65536)
        config = {
            "servers": {
                "preview": {"origin": connection["origin"], "ca_file": str(ca)}
            },
            "manifests": [],
        }
        access(target, data=(json.dumps(config) + "\n").encode("utf-8"), limit=16384)
    finally:
        lock.close()


def main():
    if sys.platform != "win32":
        raise SystemExit("This helper configures the Windows native preview only")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-kit", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path)
    args = parser.parse_args()
    try:
        if not args.state_dir and not os.environ.get("LOCALAPPDATA"):
            raise ClientError("LOCAL_STATE_UNAVAILABLE")
        state = (
            args.state_dir
            or Path(os.environ["LOCALAPPDATA"]) / "org.ptcglab.desktop.preview"
        )
        configure(args.client_kit, state)
    except ClientError as error:
        print(json.dumps({"status": "failed", "code": error.code}))
        return 1
    except (ValueError, OSError, KeyError, TypeError):
        print(json.dumps({"status": "failed", "code": "CONFIGURATION_FAILED"}))
        return 1
    print(json.dumps({"status": "passed", "identityImported": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
