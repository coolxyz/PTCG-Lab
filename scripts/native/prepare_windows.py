"""Download locked public inputs and explicitly build the pure Python parser.

Runs in a new developer-only directory; never alters global Python or a service.
There is no automatic source-build fallback and no target binary is executed.
"""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import venv

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))


def download(url, target, digest):
    if not url.startswith("https://"):
        raise ValueError("HTTPS input required")
    with (
        urllib.request.urlopen(url, timeout=60) as response,
        target.open("xb") as stream,
    ):
        shutil.copyfileobj(response, stream)
    if sha256(target.read_bytes()).hexdigest() != digest:
        raise ValueError("Downloaded input hash mismatch")


def prepare(output):
    if sys.version_info[:3] != (3, 14, 10):
        raise ValueError("Use the pinned developer Python 3.14.10 for the parser build")
    output = Path(output).absolute()
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("Unsafe preparation directory")
    output.mkdir(parents=True, exist_ok=False)
    runtime = json.loads(
        (ROOT / "scripts/native/runtime-windows-lock.json").read_text()
    )
    lock = json.loads((ROOT / "scripts/native/windows-wheel-lock.json").read_text())
    download(runtime["url"], output / "python-windows.tar.gz", runtime["sha256"])
    source = lock["pureParser"]["source"]
    archive = output / source["name"]
    download(source["url"], archive, source["sha256"])
    requirements = output / "download.lock"
    requirements.write_text(
        "\n".join(
            f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}"
            for w in lock["wheels"]
            if w["name"] != "mwparserfromhell"
        )
        + "\n",
        encoding="utf-8",
    )
    wheels = output / "wheels"
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-m",
            "pip",
            "download",
            "--no-cache-dir",
            "--require-hashes",
            "--only-binary=:all:",
            "--no-deps",
            "--platform",
            "win_amd64",
            "--python-version",
            "3.14",
            "--implementation",
            "cp",
            "--abi",
            "cp314",
            "--abi",
            "abi3",
            "--abi",
            "none",
            "-r",
            str(requirements),
            "--dest",
            str(wheels),
        ],
        check=True,
    )
    home = output / "build-env"
    venv.EnvBuilder(with_pip=True).create(home)
    python = home / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [
            str(python),
            "-I",
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--require-hashes",
            "--no-deps",
            "--only-binary=:all:",
            "-r",
            str(ROOT / "scripts/native/windows-build-requirements.lock"),
        ],
        check=True,
    )
    source_dir = output / "parser-source"
    source_dir.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(source_dir, filter="data")
    env = {**os.environ, **lock["pureParser"]["environment"]}
    env.pop("WITHOUT_EXTENSION", None)
    subprocess.run(
        [
            str(python),
            "-I",
            "-m",
            "pip",
            "wheel",
            "--no-cache-dir",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheels),
            str(source_dir / "mwparserfromhell-0.7.2"),
        ],
        env=env,
        check=True,
    )
    subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from scripts.native.windows_payload import verified_wheels; verified_wheels(Path(sys.argv[2]),json.loads(Path(sys.argv[3]).read_text()))",
            str(ROOT),
            str(wheels),
            str(ROOT / "scripts/native/windows-wheel-lock.json"),
        ],
        check=True,
    )
    return {
        "schema": 1,
        "status": "inputs-verified",
        "python": runtime["python"],
        "externalWheels": len(lock["wheels"]),
        "nativeExecution": "pending",
        "releaseReady": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.output)
    (args.output / "INPUTS.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result))
