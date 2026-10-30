"""Run component pytest with the bundled interpreter and external test-only tools."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.native.windows_payload_check import verify  # noqa: E402
from scripts.native.windows_environment import facts  # noqa: E402


def run(payload, tools, output):
    facts()  # Native Win32 guard; standard-user qualification is in the full runner.
    payload = verify(payload)
    tools = Path(tools).absolute()
    output = Path(output).absolute()
    if not tools.is_dir() or output.exists():
        raise ValueError("Use existing external tools and a NEW result directory")
    output.mkdir(parents=True)
    python = payload / "python/python.exe"
    code = "import sys,os,pathlib; p=pathlib.Path(sys.argv[1]).resolve(); import ptcg_desktop_api,ptcg_online_client,ptcg_protocol; assert all(pathlib.Path(m.__file__).resolve().is_relative_to(p) for m in (ptcg_desktop_api,ptcg_online_client,ptcg_protocol)); sys.path[:0]=[sys.argv[2],sys.argv[3]]; os.environ['PTCG_WINDOWS_NATIVE_TEST']='1'; import pytest; raise SystemExit(pytest.main([sys.argv[4],'-q','--tb=short','--junitxml='+sys.argv[5]]))"
    log = output / "pytest.log"
    with log.open("wb") as stream:
        subprocess.run(
            [
                str(python),
                "-I",
                "-B",
                "-X",
                "utf8",
                "-c",
                code,
                str(payload),
                str(tools),
                str(ROOT),
                str(ROOT / "tests/native/test_windows_native.py"),
                str(output / "results.xml"),
            ],
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    import os

    subprocess.run(
        [
            str(python),
            "-I",
            "-B",
            str(ROOT / "scripts/native/windows_report.py"),
            "--junit",
            str(output / "results.xml"),
            "--output",
            str(output / "report.json"),
        ],
        env={**os.environ, "PTCG_WINDOWS_NATIVE_TEST": "1"},
        check=True,
        timeout=30,
    )
    verify(payload)  # Tests must not mutate the shipping runtime.
    return json.loads((output / "report.json").read_text())


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("payload", "tools", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    run(a.payload, a.tools, a.output)
