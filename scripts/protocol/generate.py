"""Pinned Python/TypeScript generators; P1 builds metadata only, not live API DTOs."""

import argparse
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def generate(check=False):
    if version("datamodel-code-generator") != "0.35.0":
        raise SystemExit("Install the pinned migration requirements.lock")
    with tempfile.TemporaryDirectory() as scratch:
        output = Path(scratch) / "scaffold.py"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "datamodel_code_generator",
                "--input",
                "packages/battle-protocol/src/ptcg_protocol/schema/scaffold.schema.json",
                "--input-file-type",
                "jsonschema",
                "--output-model-type",
                "typing.TypedDict",
                "--target-python-version",
                "3.12",
                "--disable-timestamp",
                "--output",
                str(output),
            ],
            cwd=ROOT,
            check=True,
        )
        target = ROOT / "packages/battle-protocol/src/ptcg_protocol/scaffold.py"
        content = output.read_text()
        if check:
            if not target.exists() or target.read_text() != content:
                raise SystemExit("Generated Python has drifted")
        else:
            target.write_text(content)
    subprocess.run(
        ["node", "scripts/protocol/generate-ts.mjs", *(["--check"] if check else [])],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    generate(parser.parse_args().check)
