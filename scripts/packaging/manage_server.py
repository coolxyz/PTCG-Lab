"""Standalone API-kit manager: Python 3.11+ and Docker Compose v2, no repository."""

import argparse
import gzip
from hashlib import file_digest
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess


def metadata(root):
    value = json.loads((root / "MANIFEST.json").read_text())
    if (
        value.get("schema") != 1
        or value.get("kind") != "ptcg-api-compose-preview"
        or not re.fullmatch(r"ptcg-preview-[a-z0-9-]{1,64}", value.get("project", ""))
    ):
        raise ValueError("Invalid API preview manifest")
    return value


def verify(root):
    report = metadata(root)
    for name, digest in report["files"].items():
        relative = PurePosixPath(name)
        path = root / relative
        if (
            not name
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in name
            or ":" in name
            or relative.as_posix() != name
            or not path.is_file()
            or any(
                p.is_symlink()
                for p in (path, *path.parents)
                if p != root and p.is_relative_to(root)
            )
        ):
            raise ValueError("Missing or unsafe kit file")
        with path.open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError("Changed kit file: " + name)
    return report


def compose(root, *args):
    report = metadata(root)
    subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            report["project"],
            "-f",
            str(root / "compose.yaml"),
            *args,
        ],
        cwd=root,
        check=True,
    )


def images_match(report):
    for tag, expected in report["images"].items():
        value = subprocess.check_output(["docker", "image", "inspect", tag], text=True)
        image = json.loads(value)[0]
        if (
            image["Id"] != expected
            or image["Os"] + "/" + image["Architecture"] != report["platform"]
        ):
            raise ValueError("Image identity or architecture mismatch")


def load(root):
    report = verify(root)
    for name in ("api", "postgres", *(["caddy"] if report.get("tlsMode") == "acme" else [])):
        with gzip.open(root / "images" / (name + ".tar.gz"), "rb") as archive:
            with subprocess.Popen(["docker", "load"], stdin=subprocess.PIPE) as child:
                try:
                    shutil.copyfileobj(archive, child.stdin)
                finally:
                    child.stdin.close()
                if child.wait() != 0:
                    raise RuntimeError("Image import failed")
    images_match(report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("verify", "load", "up", "down", "status", "check", "check-public", "backup", "restore")
    )
    parser.add_argument("--output", type=Path, help="New private backup directory")
    parser.add_argument("--backup", type=Path, help="Private backup directory to restore into an empty deployment")
    parser.add_argument("--backup-sha256", help="Trusted SHA-256 of the backup's BACKUP.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.operation == "verify":
        verify(root)
        print(
            "Preview files verified; authenticity and manual acceptance are separate."
        )
    elif args.operation == "load":
        load(root)
    elif args.operation == "up":
        load(root)
        compose(root, "config", "--quiet")
        compose(root, "up", "--detach", "--wait", "--wait-timeout", "150")
        print("Dedicated API started at " + metadata(root)["origin"])
        if metadata(root).get("tlsMode") == "acme":
            print("Run check-public after ACME issuance to verify the public TLS chain.")
    elif args.operation == "check":
        report = verify(root)
        images_match(report)
        compose(
            root,
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            ("import os,urllib.request; r=urllib.request.Request('http://127.0.0.1:8784/readyz',headers={'Host':os.environ['PTCG_ONLINE_V2_PUBLIC_ORIGIN'].split('://',1)[1]}); urllib.request.build_opener(urllib.request.ProxyHandler({})).open(r,timeout=5); print('internal readyz passed')" if report.get("tlsMode") == "acme" else "import os,ssl,urllib.request; r=urllib.request.Request('https://127.0.0.1:8784/readyz',headers={'Host':os.environ['PTCG_ONLINE_V2_PUBLIC_ORIGIN'].split('://',1)[1]}); urllib.request.urlopen(r,context=ssl.create_default_context(cafile='/run/tls/ca.pem'),timeout=5); print('readyz passed')"),
        )
    elif args.operation == "check-public":
        import urllib.request
        report = verify(root)
        if report.get("tlsMode") != "acme":
            raise ValueError("Public TLS check requires an ACME kit")
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(report["origin"] + "/readyz", timeout=10) as response:
            if response.status != 200 or not json.load(response).get("ready"):
                raise ValueError("Public API is not ready")
        print("Public TLS chain and readyz passed")
    elif args.operation in {"backup", "restore"}:
        if __package__:
            from scripts.packaging import server_backup
        else:
            import server_backup
        report = verify(root)
        images_match(report)
        if args.operation == "backup":
            if args.output is None:
                parser.error("backup requires --output <new private directory>")
            server_backup.backup(root, report, args.output)
            print("Private database backup completed; contains credentials and private match data.")
        else:
            if args.backup is None or args.backup_sha256 is None:
                parser.error("restore requires --backup and --backup-sha256")
            server_backup.restore(root, report, args.backup, args.backup_sha256)
            print("Restored into an empty dedicated database; run up/check before use.")
    elif args.operation == "down":
        # Never add --volumes or touch any other project.
        compose(root, "down")
    else:
        compose(root, "ps")


if __name__ == "__main__":
    main()
