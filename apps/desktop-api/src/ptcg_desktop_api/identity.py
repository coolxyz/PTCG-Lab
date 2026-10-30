"""Explicit operator-only import. Never exposed through HTTP or WebView commands."""

import argparse
import getpass
import json
import os
from pathlib import Path
import sys

from ptcg_online_client.contracts import ClientError
from ptcg_online_client.transport import Server
from .native import StateLock, request
from .secure_storage import import_identity


def source_guard(source, kind):
    """Coordinate with native APPs and the existing POSIX product owner."""
    root = Path(source).absolute().parent
    if kind == "native":
        return StateLock(root)
    if kind != "browser":
        raise ClientError("IDENTITY_SOURCE_KIND")
    product_lock = root / "product.lock"
    if os.name == "posix" and (product_lock.exists() or product_lock.is_symlink()):
        from ptcg_online_client.vault import PosixFiles

        return PosixFiles(root, "product")
    return None


def migrate(source, state, profile, *, source_kind="browser"):
    state = Path(state).absolute()
    lock = StateLock(state)
    original = None
    try:
        original = source_guard(source, source_kind)
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
        if source_kind == "browser":
            import_identity(source, state / "credentials", server)
        else:
            import_identity(
                source, state / "credentials", server, source_kind=source_kind
            )
    finally:
        if original:
            original.close()
        lock.close()


def transfer_main(argv):
    from .identity_transfer import export_identity, import_bundle

    parser = argparse.ArgumentParser(
        description="Explicit password-encrypted identity handoff"
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    export = commands.add_parser("export")
    export.add_argument("--source", type=Path, required=True)
    export.add_argument("--config", type=Path, required=True)
    export.add_argument("--profile", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--source-kind", choices=["browser", "native"], required=True)
    restore = commands.add_parser("import")
    restore.add_argument("--bundle", type=Path, required=True)
    restore.add_argument("--state-dir", type=Path, required=True)
    restore.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    lock = None
    try:
        if not sys.stdin.isatty():
            raise ClientError("IDENTITY_INTERACTIVE_REQUIRED")
        if args.operation == "export":
            lock = source_guard(args.source, args.source_kind)
            if args.config.is_symlink() or args.config.stat().st_size > 16384:
                raise ValueError()
            config = json.loads(args.config.read_text(encoding="utf-8"))
        else:
            lock = StateLock(args.state_dir.absolute())
            from .sealed_file import access

            config = json.loads(access(args.state_dir / "connection.json", limit=16384))
        server = Server(**config["servers"][args.profile])
        if not server.origin.startswith("https://"):
            raise ValueError()
        password = getpass.getpass("Transfer password (12+ characters): ")
        if args.operation == "export":
            if password != getpass.getpass("Repeat transfer password: "):
                raise ClientError("IDENTITY_PASSWORD_MISMATCH")
            export_identity(
                args.source, args.output, server, password, source_kind=args.source_kind
            )
        else:
            import_bundle(args.bundle, args.state_dir / "credentials", server, password)
    except ClientError as error:
        print(json.dumps({"status": "failed", "code": error.code}))
        return 1
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        print(json.dumps({"status": "failed", "code": "IDENTITY_TRANSFER_FAILED"}))
        return 1
    finally:
        if lock:
            lock.close()
    print(json.dumps({"status": "passed", "sourceUnchanged": True}))
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] in {"export", "import"}:
        return transfer_main(sys.argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--source-kind", choices=["browser", "native"], default="browser"
    )
    args = parser.parse_args()
    try:
        migrate(args.source, args.state_dir, args.profile, source_kind=args.source_kind)
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
