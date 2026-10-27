"""Explicit operator-only import. Never exposed through HTTP or WebView commands."""

import argparse
import json
from pathlib import Path

from ptcg_online_client.contracts import ClientError
from ptcg_online_client.transport import Server
from .native import StateLock, request
from .secure_storage import import_identity


def migrate(source, state, profile):
    state = Path(state).absolute()
    lock = StateLock(state)
    try:
        config_path = state / "connection.json"
        info = config_path.lstat()
        if config_path.is_symlink() or info.st_size > 16384:
            raise ValueError()
        config = json.loads(config_path.read_text())
        request(
            {
                "schema": 1,
                "type": "start",
                "nonce": "a" * 64,
                "stateDir": str(state),
                "config": config,
            }
        )
        server = Server(**config["servers"][profile])
        if not server.origin.startswith("https://"):
            raise ValueError()
        import_identity(source, state / "credentials", server)
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    try:
        migrate(args.source, args.state_dir, args.profile)
    except ClientError as error:
        print(json.dumps({"status": "failed", "code": error.code}))
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "failed", "code": "IDENTITY_IMPORT_FAILED"}))
        return 1
    print(json.dumps({"status": "passed", "sourceUnchanged": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
