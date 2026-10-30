"""Full B4 Windows build and automation on a native standard-user workstation.

Explicit approved assets only. No server URL, user profile, deployment or service
control inputs. All generated inputs, binaries and private proofs stay in a NEW run.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.native.windows_environment import facts, eligible  # noqa: E402
from scripts.packaging.source_identity import source_digest  # noqa: E402
from scripts.native.layout import PROJECTS  # noqa: E402


def new_run(path):
    path = Path(path).absolute()
    if path.exists() or any(
        p.is_symlink() or p.is_junction() for p in (path, *path.parents)
    ):
        raise ValueError("Use a NEW local result directory without links")
    # Never select an output containing the checkout (or another existing tree).
    if ROOT.is_relative_to(path):
        raise ValueError("Output cannot contain the checkout")
    path.mkdir(parents=True, mode=0o700)
    return path


def pipeline(assets, output):
    environment = facts()
    if not eligible(environment):
        raise ValueError(
            "Native Windows x64, Python 3.14.10 and a genuine non-admin account required"
        )
    assets = Path(assets).absolute()
    if not assets.is_dir() or any(
        p.is_symlink() or p.is_junction() for p in (assets, *assets.parents)
    ):
        raise ValueError("Explicit approved local assets required")
    if not subprocess.check_output(["rustc", "--version"], text=True).startswith(
        "rustc 1.90.0 "
    ):
        raise ValueError("Use Rust 1.90.0 with the MSVC target and SDK")
    npm = shutil.which("npm.cmd")
    if not npm:
        raise ValueError("Node/npm build tools required; end users need neither")
    output = new_run(output)

    def write(name, value):
        (output / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    write("environment.json", environment)
    source = source_digest(ROOT)
    stage = "prepare-inputs"
    env = {
        **os.environ,
        "PTCG_WINDOWS_NATIVE_TEST": "1",
        "CARGO_TARGET_DIR": str(output / "rust-target"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.pop("PYTHONPATH", None)

    def command(name, args, *, cwd=ROOT, timeout=1800):
        nonlocal stage
        stage = name
        with (output / (name + ".log")).open("wb") as log:
            subprocess.run(
                [str(a) for a in args],
                cwd=cwd,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=True,
            )

    def script(name, file, *args, python=sys.executable, timeout=1800):
        command(
            name,
            [python, "-I", "-B", "-X", "utf8", ROOT / file, *args],
            timeout=timeout,
        )

    try:
        script(
            "prepare-inputs",
            "scripts/native/prepare_windows.py",
            "--output",
            output / "inputs",
        )
        python = output / "inputs/build-env/Scripts/python.exe"
        command("frontend-install", [npm, "ci", "--no-audit", "--no-fund"])
        command("frontend-build", [npm, "run", "product:build"])
        wheels = output / "project-wheels"
        for index, project in enumerate(PROJECTS):
            command(
                "wheel-" + str(index),
                [
                    python,
                    "-I",
                    "-B",
                    "-m",
                    "pip",
                    "wheel",
                    "--no-index",
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    wheels,
                    ROOT / project,
                ],
            )
        script(
            "payload",
            "scripts/native/windows_payload.py",
            "--runtime",
            output / "inputs/python-windows.tar.gz",
            "--wheelhouse",
            output / "inputs/wheels",
            "--internal",
            wheels,
            "--assets",
            assets,
            "--output",
            output / "payload",
            python=python,
        )
        command(
            "test-tools",
            [
                python,
                "-I",
                "-B",
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--no-deps",
                "--only-binary=:all:",
                "--target",
                output / "test-tools",
                "-r",
                ROOT / "scripts/native/windows-test-requirements.lock",
            ],
        )
        script(
            "python-tests",
            "scripts/native/windows_tests.py",
            "--payload",
            output / "payload",
            "--tools",
            output / "test-tools",
            "--output",
            output / "python",
            python=python,
        )
        script(
            "rust-tests",
            "scripts/native/windows_shell_check.py",
            "--output",
            output / "rust.json",
            python=python,
        )
        script(
            "release-build",
            "scripts/packaging/windows.py",
            "--payload",
            output / "payload",
            "--output",
            output / "bundle",
            python=python,
            timeout=3600,
        )
        script(
            "gui-smoke",
            "scripts/native/desktop_smoke.py",
            "--app",
            output / "bundle/PTCG Lab Preview.exe",
            "--output",
            output / "gui",
            python=python,
            timeout=300,
        )
        stage = "collect-evidence"
        if source_digest(ROOT) != source or facts() != environment:
            raise ValueError("Sources or native environment changed during acceptance")
        # Isolated build interpreter supplies packaging, never host/global install.
        script(
            "evidence",
            "scripts/native/windows_evidence_cli.py",
            "--run",
            output,
            python=python,
        )
        return json.loads((output / "REPORT.json").read_text(encoding="utf-8"))
    except BaseException:
        write(
            "FAILED.json",
            {
                "schema": 1,
                "stage": stage,
                "status": "failed",
                "sourceSha256": source,
                "releaseReady": False,
            },
        )
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assets", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    pipeline(a.assets, a.output)
    print("B4 automation passed; manual Windows acceptance and release remain pending.")
