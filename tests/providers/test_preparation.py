import json

import pytest
from ptcg_engine_provider.bundle import DirectoryEngineProvider
from ptcg_engine_provider.values import ProviderError, canonical, sha256
from scripts.migration.prepare_rule_bundle import INPUTS, prepare

from .synthetic import catalog, payloads


def legacy_source(root):
    hashes = {}
    for name, role, content in payloads():
        if role in INPUTS:
            target = root / INPUTS[role]
        elif role == "license":
            target = root / "runtime/engine/UPSTREAM-LICENSE"
        else:
            target = root / "runtime/engine" / name
            hashes[name] = sha256(content)
        if role == "catalog":
            content = canonical(catalog())
        elif role == "mapping":
            content = canonical(
                {
                    "printings": {c["printingId"]: c for c in catalog()["cards"]},
                    "decks": {"private-sentinel": []},
                }
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    overlay = canonical(hashes)
    path = root / "artifacts/engine/overlay-hashes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(overlay)
    for name in [
        "var/app.sqlite",
        ".catalog/account.json",
        "models/weights.bin",
        "runtime/engine/unlisted.py",
    ]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private-sentinel")
    return sha256(overlay)


def test_preparation_copies_only_verified_explicit_inputs(tmp_path):
    source = tmp_path / "source"
    output = tmp_path / "output"
    pin = legacy_source(source)
    before = {
        str(p.relative_to(source)): sha256(p.read_bytes())
        for p in source.rglob("*")
        if p.is_file()
    }
    report = prepare(source, output, expected_overlay_sha256=pin)
    assert not report["userDatabasesCopied"] and not report["engineExecuted"]
    assert report["containsRestrictedAssets"] and not report["productionReady"]
    bundle = DirectoryEngineProvider(output, report["manifestSha256"]).load()
    assert len(bundle.engine_sources()) == 1
    assert "images" not in bundle.catalog.card("synthetic-basic")
    for path in output.rglob("*"):
        if path.is_file():
            assert b"private-sentinel" not in path.read_bytes()
    assert before == {
        str(p.relative_to(source)): sha256(p.read_bytes())
        for p in source.rglob("*")
        if p.is_file()
    }
    with pytest.raises(ProviderError, match="OUTPUT_EXISTS"):
        prepare(source, output, expected_overlay_sha256=pin)


@pytest.mark.parametrize("kind", ["pin", "code", "missing", "catalog-format"])
def test_preparation_fails_before_publishing_output(tmp_path, kind):
    source = tmp_path / "source"
    output = tmp_path / "output"
    pin = legacy_source(source)
    if kind == "pin":
        pin = "f" * 64
    elif kind == "code":
        (source / "runtime/engine/ptcg/__init__.py").write_text("tampered")
    elif kind == "missing":
        (source / INPUTS["scope"]).unlink()
    else:
        p = source / INPUTS["catalog"]
        value = json.loads(p.read_text())
        value["formatId"] = "incompatible"
        p.write_bytes(canonical(value))
    with pytest.raises(ProviderError):
        prepare(source, output, expected_overlay_sha256=pin)
    assert not output.exists()


def test_preparation_cannot_write_assets_into_source_tree(tmp_path):
    source = tmp_path / "source"
    pin = legacy_source(source)
    with pytest.raises(ProviderError, match="PRIVATE_OUTPUT_REQUIRED"):
        prepare(source, source / "packages/leaked-assets", expected_overlay_sha256=pin)


def test_display_changes_do_not_change_prepared_bundle_identity(tmp_path):
    source = tmp_path / "source"
    pin = legacy_source(source)
    first = prepare(source, tmp_path / "first", expected_overlay_sha256=pin)
    for role in ("catalog", "mapping"):
        path = source / INPUTS[role]
        raw = json.loads(path.read_text())
        rows = raw["cards"] if role == "catalog" else raw["printings"].values()
        for card in rows:
            card["images"] = [{"url": "new-image"}]
            card["cnName"] = "展示名称修改"
        path.write_bytes(canonical(raw))
    second = prepare(source, tmp_path / "second", expected_overlay_sha256=pin)
    assert first["bundleId"] == second["bundleId"]
    assert first["manifestSha256"] == second["manifestSha256"]
