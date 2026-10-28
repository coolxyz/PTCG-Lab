"""Standalone test-kit manager: Python 3.11+ and Docker Compose v2, no repository."""

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
    for name in ("api", "postgres"):
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
        "operation", choices=("verify", "load", "up", "down", "status", "check")
    )
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
        print("Dedicated preview ready at " + metadata(root)["origin"])
    elif args.operation == "check":
        images_match(verify(root))
        compose(
            root,
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            "import os,ssl,urllib.request; r=urllib.request.Request('https://127.0.0.1:8784/readyz',headers={'Host':os.environ['PTCG_ONLINE_V2_PUBLIC_ORIGIN'].split('://',1)[1]}); urllib.request.urlopen(r,context=ssl.create_default_context(cafile='/run/tls/ca.pem'),timeout=5); print('readyz passed')",
        )
    elif args.operation == "down":
        # Never add --volumes or touch any other project.
        compose(root, "down")
    else:
        compose(root, "ps")


if __name__ == "__main__":
    main()
