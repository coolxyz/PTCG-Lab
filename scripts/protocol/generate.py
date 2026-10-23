"""Generate Python and TypeScript contracts from the packaged JSON Schemas."""

import argparse
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ("scaffold", "v2")


def generate(check=False):
    if version("datamodel-code-generator") != "0.35.0":
        raise SystemExit("Install the pinned migration requirements.lock")
    with tempfile.TemporaryDirectory() as scratch:
        for name in SCHEMAS:
            output = Path(scratch) / (name + ".py")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "datamodel_code_generator",
                    "--input",
                    f"packages/battle-protocol/src/ptcg_protocol/schema/{name}.schema.json",
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
            target = ROOT / f"packages/battle-protocol/src/ptcg_protocol/{name}.py"
            content = output.read_text()
            if check:
                if not target.exists() or target.read_text() != content:
                    raise SystemExit(f"Generated Python has drifted: {name}")
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
