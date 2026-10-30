"""Full-product kit verification, platform-local installation and unified startup.

Copied to the kit root as manage.py. Python 3.14+; Node and external Git are not required.
"""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import platform
import subprocess
import sys
import venv

CHECKS = (
    "singleLocalApp",
    "sharedDeckPractice",
    "sharedDeckOnline",
    "inviteAndReady",
    "naturalEnd",
    "bothSeatReplay",
    "editAfterStart",
    "collectionAndBuilder",
    "refreshAndReopen",
    "restartSameIdentity",
    "disconnectAndRecover",
    "serverOfflineLocalFeatures",
    "offlineArtwork",
    "missingArtworkFallback",
    "oldResourceReplay",
    "noRemoteArtworkRequests",
    "authorizationIsolation",
)


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "KIT-MANIFEST.json").read_bytes())
    if manifest.get("schema") != 1 or manifest.get("stage") != "P5.5-D":
        raise ValueError("Unsupported product kit")
    for name, digest in manifest["files"].items():
        relative = PurePosixPath(name)
        if (
            not name
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in name
            or ":" in name
            or relative.as_posix() != name
        ):
            raise ValueError("Unsafe kit path")
        path = root / name
        if (
            any(
                p.is_symlink()
                for p in (path, *path.parents)
                if p != root and p.is_relative_to(root)
            )
            or not path.is_file()
        ):
            raise ValueError("Missing or linked kit file: " + name)
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Changed kit file: " + name)
    return manifest


def configuration(root):
    root = Path(root).resolve()
    config = {
        "servers": {
            "lan": {
                "origin": json.loads((root / "connection.json").read_text())["origin"],
                "ca_file": str(root / "ca.pem"),
            }
        },
        "manifests": [],
    }
    directory = root / "app/var"
    directory.mkdir(exist_ok=True)
    path = directory / "product-connection.json"
    raw = json.dumps(config, sort_keys=True) + "\n"
    if path.exists() and path.read_text() != raw:
        raise ValueError(
            "APP path or trust changed. Stop the APP and review product-connection.json before replacing it; retain the identity directory."
        )
    if not path.exists():
        with path.open("x") as file:
            file.write(raw)
        path.chmod(0o600)
    return path


def install(root, wheelhouse=None):
    manifest = verify(root)
    if sys.version_info < (3, 14) or sys.platform == "win32":
        raise ValueError("Use Python 3.14+ on macOS or inside WSL2")
    app = root / "app"
    python = app / ".venv/bin/python"
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(app / ".venv")
    command = [str(python), "-m", "pip", "install"]
    if wheelhouse:
        command += ["--no-index", "--find-links", str(Path(wheelhouse).resolve())]
    subprocess.run(
        command
        + [
            "-r",
            str(app / "apps/api/requirements.lock"),
            "-r",
            str(app / "apps/desktop-api/requirements.lock"),
            *[
                str(root / name)
                for name in sorted(manifest["files"])
                if name.startswith("wheels/") and name.endswith(".whl")
            ],
        ],
        check=True,
        cwd=app,
    )
    subprocess.run(
        [
            str(python),
            str(app / "scripts/product/resources.py"),
            "install",
            "--pack",
            str(root / "artwork"),
            "--home",
            str(app / ".catalog/sync"),
        ],
        check=True,
    )
    config = configuration(root)
    subprocess.run(
        [
            str(python),
            str(app / "scripts/start.py"),
            "--check",
            "--online-config",
            str(config),
        ],
        check=True,
        cwd=app,
    )
    print("Installed. Start this APP with: python3 manage.py run")


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("verify", "install", "run", "reopen", "report")
    )
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--port", type=int, default=18789)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("acceptance.json"))
    args = parser.parse_args()
    if args.action == "verify":
        value = verify(root)
        print(
            json.dumps(
                {
                    "verifiedFiles": len(value["files"]),
                    "physicalDeviceAcceptance": "pending",
                }
            )
        )
    elif args.action == "install":
        install(root, args.wheelhouse)
    elif args.action == "report":
        manifest = verify(root)
        result = {
            "stage": "P5.5-D",
            "kitManifestSha256": sha256(
                (root / "KIT-MANIFEST.json").read_bytes()
            ).hexdigest(),
            "sourceCommit": manifest["sourceCommit"],
            "platform": platform.platform(),
            "python": platform.python_version(),
            "browser": "fill manually",
            "physicalDevice": "fill manually",
            "peerDevice": "fill manually",
            "checks": {name: {"status": "pending", "evidence": ""} for name in CHECKS},
            "displayIssues": "deferred until after P5.5",
        }
        with args.report.open("x") as file:
            json.dump(result, file, ensure_ascii=False, indent=2)
            file.write("\n")
    else:
        if not 1 <= args.port <= 65535:
            parser.error("Invalid port")
        verify(root)
        config = configuration(root)
        app = root / "app"
        python = app / ".venv/bin/python"
        if not python.is_file():
            parser.error("Run install first on this device")
        command = [
            str(python),
            str(app / "scripts/start.py"),
            "--port",
            str(args.port),
            "--online-config",
            str(config),
        ]
        if args.no_browser:
            command.append("--no-browser")
        if args.action == "reopen":
            command.append("--reopen")
        os.chdir(app)
        os.execv(str(python), command)


if __name__ == "__main__":
    main()
