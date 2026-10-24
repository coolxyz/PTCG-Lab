"""Run unchanged, pure legacy rule tests against an installed core in scratch.

The fixture-only Adapter supplies explicit original preset lines. No production
aliases/defaults are changed. This runner never imports an app or AI agent.
"""

import argparse
import ast
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SELECTED = (
    "checkup",
    "field_effects",
    "coin_branches",
    "damage_events",
    "reaction_choices",
    "special_energy",
    "healing_checkup",
    "dual_and_locks",
    "suppression",
)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--pin", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    queue = ["tests/rules/test_rules.py", "tests/rules/test_effects.py"] + [
        f"tests/cardpool/test_{n}.py" for n in SELECTED
    ]
    sources = {}
    while queue:
        name = queue.pop()
        if name in sources:
            continue
        text = (a.source / name).read_text()
        sources[name] = text
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom):
                imported = node.module or ""
                if imported in {
                    "scripts.cardpool.attack_rules",
                    "scripts.cardpool.extended_math_rules",
                }:
                    queue.append(imported.replace(".", "/") + ".py")
                elif imported.startswith(
                    (
                        "apps.",
                        "scripts.",
                        "packages.ai",
                        "packages.battle",
                        "packages.simulation",
                    )
                ):
                    raise ValueError("Non-pure test dependency: " + imported)
                if imported.startswith("tests."):
                    queue.append(imported.replace(".", "/") + ".py")
    with tempfile.TemporaryDirectory(prefix="ptcg-core-regression-") as temp:
        root = Path(temp)
        (root / "tests/cardpool").mkdir(parents=True)
        for path in (
            "tests/__init__.py",
            "tests/cardpool/__init__.py",
            "scripts/__init__.py",
            "scripts/cardpool/__init__.py",
        ):
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text("")
        for name, text in sources.items():
            target = root / (
                Path(name).name if name.startswith("tests/rules/") else name
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        decks = []
        for name in ("gholdengo_ex", "charizard_ex"):
            lines = (
                (a.source / f"runtime/engine/ptcg/decks/{name}.txt")
                .read_text()
                .splitlines()
            )
            decks.append(
                [line for line in lines if line.strip() and line.strip()[0].isdigit()]
            )
        config = dict(bundle=str(a.bundle.resolve()), pin=a.pin, decks=decks)
        (root / "inputs.json").write_text(json.dumps(config))
        # Copy only the DTO test chooser, not any APP/AI module.
        shutil.copyfile(
            Path(__file__).with_name("p2d_worker.py"), root / "acceptance_helpers.py"
        )
        (root / "conftest.py").write_text("""
import json
from pathlib import Path
from ptcg_engine_provider.bundle import DirectoryEngineProvider
from ptcg_core.factory import CoreFactory
from ptcg_core.compat import install_legacy_imports
from acceptance_helpers import choose
config=json.loads(Path(__file__).with_name('inputs.json').read_text())
factory=CoreFactory(DirectoryEngineProvider(Path(config['bundle']),config['pin']).load())
install_legacy_imports(factory)
from ptcg_core.rules.adapter import Adapter as RealAdapter
import packages.rules.adapter as adapter_module
import packages.engine_adapter.base as base_module
class FixtureAdapter(RealAdapter):
    def __init__(self, seed=0, deck1=None, deck2=None, provenance=None):
        super().__init__(seed,config['decks'][0] if deck1 is None else deck1,config['decks'][1] if deck2 is None else deck2,provenance)
adapter_module.Adapter=FixtureAdapter
base_module.policy=choose
""")
        with (a.output / "tests.log").open("w") as log:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "test_rules.py",
                    "test_effects.py",
                    "tests/cardpool",
                    "-q",
                    "--junitxml=" + str((a.output / "tests.xml").resolve()),
                ],
                cwd=root,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    (a.output / "sources.json").write_text(json.dumps(sorted(sources), indent=2) + "\n")


if __name__ == "__main__":
    main()
