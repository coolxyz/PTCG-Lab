"""Completed-phase attestations fail on missing evidence or changed source."""

import json
from pathlib import Path
import shutil

from scripts.migration.check_p2d import check, source_hashes

ROOT = Path(__file__).resolve().parents[2]


def test_missing_acceptance_is_not_completion(tmp_path):
    (tmp_path / "releases").mkdir()
    (tmp_path / "releases/migration-status.json").write_text(
        json.dumps({"subphases": {"P2-D": "completed"}})
    )
    assert "without acceptance evidence" in check(tmp_path)[0]


def test_accepted_source_and_evidence_are_bound(tmp_path):
    report = ROOT / "releases/p2d-acceptance.json"
    # This test is only used after completion has produced the versioned report.
    assert report.is_file()
    for name in [
        *source_hashes(ROOT),
        "releases/p2d-acceptance.json",
        "releases/migration-status.json",
    ]:
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
    assert check(tmp_path) == []
    (tmp_path / "packages/battle-core/src/ptcg_core/changed_rule.py").write_text(
        '"""unaccepted source"""\n'
    )
    assert any("source drift" in e for e in check(tmp_path))
    (tmp_path / "packages/battle-core/src/ptcg_core/changed_rule.py").unlink()
    evidence = json.loads((tmp_path / "releases/p2d-acceptance.json").read_text())
    evidence["crossProcessResumeCount"] = 0
    (tmp_path / "releases/p2d-acceptance.json").write_text(json.dumps(evidence))
    assert any("incomplete" in e for e in check(tmp_path))
