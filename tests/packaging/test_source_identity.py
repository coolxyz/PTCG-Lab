"""Current product identity excludes state, includes maintained rule changes."""
from scripts.packaging.source_identity import source_digest
from scripts.public.policy import allowed_source


def test_identity_binds_rules_but_ignores_local_outputs(tmp_path):
    rule = tmp_path / "packages/rules/current.py"
    rule.parent.mkdir(parents=True)
    rule.write_text("VALUE = 1\n")
    before = source_digest(tmp_path)
    for name in ("var/result.json", "release/temporary.json", "data/test.json"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
    assert source_digest(tmp_path) == before
    rule.write_text("VALUE = 2\n")
    assert source_digest(tmp_path) != before


def test_development_records_are_not_publishable():
    for name in ("releases/phase.json", "benchmarks/online/probe.py", "scripts/migration/gate.py", "docs/P8开发记录.md", "data/catalog/catalog.json", "tests/sync/real-recovery.json"):
        assert not allowed_source(name)
