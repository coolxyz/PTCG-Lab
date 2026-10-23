"""Only new core changes may evolve rules; legacy runtime stays byte-identical."""

import json
from pathlib import Path
import shutil

from scripts.migration.check_legacy_freeze import check

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_legacy_and_new_rule_rejected(tmp_path):
    manifest = ROOT / "releases/legacy-core-freeze.json"
    rows = json.loads(manifest.read_text())["files"]
    for name in ["releases/legacy-core-freeze.json", *rows]:
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
    assert check(tmp_path) == []
    (tmp_path / "packages/rules/adapter.py").write_text(
        "# accidental second implementation\n"
    )
    assert any("frozen legacy" in e for e in check(tmp_path))
    (tmp_path / "packages/rules/new_effect.py").write_text("")
    assert any("new implementation" in e for e in check(tmp_path))
