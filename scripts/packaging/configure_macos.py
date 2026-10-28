"""Explicit preview connection setup; preserve any existing configuration/identity."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def configure(root, state):
    from ptcg_desktop_api.native import StateLock

    root, state = Path(root).resolve(), Path(state).absolute()
    connection = json.loads((root / "client.json").read_text())
    if set(connection) != {"origin", "caFile"} or connection["caFile"] != "tls/ca.pem":
        raise ValueError("Invalid preview connection")
    from ptcg_online_client.transport import Server

    Server(connection["origin"], ca_file=str(root / "tls/ca.pem"))
    lock = StateLock(state)
    try:
        target = state / "connection.json"
        if target.exists() or target.is_symlink():
            raise ValueError(
                "Existing configuration retained. Close the APP and review it manually before changing servers."
            )
        ca = state / "preview-server-ca.pem"
        # Existing CA is never overwritten, including a dangling symbolic link.
        if ca.exists() or ca.is_symlink():
            raise ValueError("Existing CA retained; review the profile before retrying")
        with ca.open("xb") as stream:
            os.chmod(ca, 0o600)
            with (root / "tls/ca.pem").open("rb") as source:
                shutil.copyfileobj(source, stream)
        config = {
            "servers": {
                "preview": {"origin": connection["origin"], "ca_file": str(ca)}
            },
            "manifests": [],
        }
        with target.open("x") as stream:
            os.chmod(target, 0o600)
            json.dump(config, stream)
            stream.write("\n")
    finally:
        lock.close()


def main():
    if sys.platform != "darwin":
        raise SystemExit("This helper configures the macOS preview only.")
    state = Path.home() / "Library/Application Support/org.ptcglab.desktop.preview"
    try:
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).with_name("manage.py")),
                "verify",
            ],
            check=True,
        )
        configure(Path(__file__).resolve().parent, state)
    except (ValueError, OSError) as error:
        print(str(error))
        return 1
    print(
        "Preview server configured. Open PTCG Lab Preview.app; no existing identity was imported."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
