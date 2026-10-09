from pathlib import Path
from scripts.public.export_source import source_files


def test_public_export_excludes_state_data_and_history():
    names = source_files()
    assert all(
        not n.startswith(
            (
                ".git/",
                ".catalog/",
                "var/",
                "data/",
                "artifacts/",
                "outputs/",
                "vendor/",
                "runtime/",
                "tests/fixtures/",
            )
        )
        for n in names
    )
    assert not {
        "MIGRATION.json",
        "TRANSFER-MANIFEST.json",
        "tests/sync/real-recovery.json",
    } & set(names)
    assert {
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "licenses/ptcg-engine-MIT.txt",
        "scripts/public/import_local_assets.py",
        "rulesets/cn-standard-2026-09-16.json",
    } <= set(names)
    assert all(Path(n).is_file() for n in names)
