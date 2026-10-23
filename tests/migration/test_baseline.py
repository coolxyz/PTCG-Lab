
import pytest
from scripts.migration.baseline import capture


def put(root, name, content):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_baseline_copies_only_source_and_explicit_assets(tmp_path):
    root = tmp_path / "source"
    put(root, "README.md", "public code")
    put(root, "apps/api/main.py", "app code")
    put(root, "data/catalog/catalog.json", "{}")
    put(root, "runtime/engine/ptcg/core/envs.py", "engine code")
    put(root, "artifacts/engine/overlay-hashes.json", "{}")
    put(root, "var/app.sqlite", "private-sentinel")
    put(root, ".catalog/session.json", "private-sentinel")
    put(root, ".env", "private-sentinel")
    output = tmp_path / "baseline"
    result = capture(output, root, stage=True, assets=True)
    assert result["sourceCommit"] is None
    assert result["assetFiles"] == 3
    assert not result["userDatabasesCopied"]
    assert (output / "app/data/catalog/catalog.json").exists()
    assert not (output / "app/var").exists()
    assert not (output / "app/.catalog").exists()
    assert not (output / "app/.env").exists()
    with pytest.raises(ValueError, match="exists"):
        capture(output, root)


def test_baseline_missing_assets_fail_without_creating_output(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    output = tmp_path / "baseline"
    with pytest.raises(ValueError, match="Missing"):
        capture(output, root, stage=True, assets=True)
    assert not output.exists()
