from dataclasses import FrozenInstanceError
import json
import shutil
import subprocess
import sys

import pytest
from ptcg_engine_provider.bundle import DirectoryEngineProvider, read_asset
from ptcg_engine_provider.catalog import MemoryCatalog
from ptcg_engine_provider.values import ProviderError, canonical, parse_json

from .synthetic import catalog, payloads, write_bundle, write_manifest


@pytest.fixture
def assets(tmp_path):
    pin = write_bundle(tmp_path)
    return tmp_path, pin


def load(assets):
    return DirectoryEngineProvider(*assets).load()


def test_catalog_normalization_and_caller_mutation():
    raw = catalog()
    provider = MemoryCatalog.from_catalog(raw)
    raw["cards"][0]["engineLine"] = "changed"
    entries = [
        {"printingId": "synthetic-energy", "quantity": 1},
        {"printingId": "synthetic-basic", "quantity": 2},
        {"printingId": "synthetic-energy", "quantity": 3},
    ]
    result = provider.normalize(entries)
    assert [(e.printing_id, e.quantity) for e in result] == [
        ("synthetic-basic", 2),
        ("synthetic-energy", 4),
    ]
    entries.clear()
    assert len(result) == 2
    assert provider.card("synthetic-basic")["engineLine"] == "SyntheticBasic SYN 1"
    with pytest.raises(TypeError):
        provider.card("synthetic-basic")["engineLine"] = "changed"
    with pytest.raises(FrozenInstanceError):
        result[0].quantity = 0


@pytest.mark.parametrize(
    "entries,code",
    [
        ({}, "BAD_ENTRIES"),
        ([{}] * 501, "BAD_ENTRIES"),
        ([{"printingId": "unknown", "quantity": 1}], "UNKNOWN_PRINTING"),
        ([{"printingId": [], "quantity": 1}], "UNKNOWN_PRINTING"),
        ([{"printingId": "synthetic-basic", "quantity": True}], "BAD_QUANTITY"),
        ([{"printingId": "synthetic-basic", "quantity": 0}], "BAD_QUANTITY"),
        ([{"printingId": "synthetic-basic", "quantity": 1.0}], "BAD_QUANTITY"),
        ([{"printingId": "synthetic-basic", "quantity": 61}], "BAD_QUANTITY"),
        ([{"printingId": "synthetic-basic", "quantity": 31}] * 2, "BAD_QUANTITY"),
        (
            [{"printingId": "synthetic-basic", "quantity": 1, "engineLine": "evil"}],
            "BAD_ENTRY",
        ),
    ],
)
def test_invalid_deck_input(entries, code):
    with pytest.raises(ProviderError, match=code):
        MemoryCatalog.from_catalog(catalog()).normalize(entries)


def test_display_only_changes_do_not_change_semantic_catalog():
    raw = catalog()
    before = MemoryCatalog.from_catalog(raw).catalog_version
    raw["cards"][0]["images"] = [{"url": "different"}]
    raw["cards"][0]["cnName"] = "另一张展示图"
    assert MemoryCatalog.from_catalog(raw).catalog_version == before
    raw["cards"][0]["engineLine"] = "Different SYN 3"
    assert MemoryCatalog.from_catalog(raw).catalog_version != before


def test_unreviewed_and_multiline_mapping_rejected():
    for field, value in [
        ("engineLine", "first\nsecond"),
        ("engineLine", None),
        ("effectStatus", "unknown"),
        ("effectEvidence", []),
    ]:
        raw = catalog()
        raw["cards"][0][field] = value
        with pytest.raises(ProviderError, match="ENGINE_MAPPING_UNREVIEWED"):
            MemoryCatalog.from_catalog(raw).resolve_engine_lines(
                [{"printingId": "synthetic-basic", "quantity": 1}]
            )


def test_bundle_is_frozen_and_source_edits_cannot_change_existing_games(assets):
    bundle = load(assets)
    with pytest.raises(TypeError):
        bundle.documents["format"].value["allowedMarks"][0] = "X"
    copy = bundle.documents["format"].copy()
    copy["allowedMarks"].append("X")
    assert bundle.documents["format"].value["allowedMarks"] == ("G",)
    before = bundle.engine_sources()["ptcg/__init__.py"]
    path = assets[0] / "assets/ptcg/__init__.py"
    path.write_bytes(b"x" * len(before))
    assert bundle.engine_sources()["ptcg/__init__.py"] == before
    with pytest.raises(ProviderError, match="ASSET_HASH_MISMATCH"):
        load(assets)


def test_provider_constructor_and_import_do_not_read_assets(tmp_path):
    provider = DirectoryEngineProvider(tmp_path / "missing", "a" * 64)
    with pytest.raises(ProviderError, match="ASSET_UNAVAILABLE"):
        provider.load()
    # Import from an empty cwd; deny file opening during provider module import.
    code = """
import sys, pathlib
import dataclasses, hashlib, json, re, types, collections, typing, os, stat, datetime
def deny(event, args):
    if event == "open" and not str(args[0]).endswith((".py", ".pyc")):
        raise AssertionError("import read an asset")
sys.addaudithook(deny)
from ptcg_engine_provider.bundle import DirectoryEngineProvider
from ptcg_engine_provider.catalog import MemoryCatalog
assert "ptcg" not in sys.modules and "packages" not in sys.modules
"""
    subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, check=True)


def test_relocation_preserves_logical_identity(assets, tmp_path):
    first = load(assets)
    moved = tmp_path / "relocated"
    shutil.copytree(assets[0] / "assets", moved / "assets")
    manifest = json.loads((assets[0] / "bundle.json").read_text())
    (moved / "assets").rename(moved / "different")
    for row in manifest["components"]:
        row["path"] = row["path"].replace("assets/", "different/", 1)
    pin = write_manifest(moved, manifest)
    second = DirectoryEngineProvider(moved, pin).load()
    assert first.bundle_id == second.bundle_id
    assert first.manifest_digest != second.manifest_digest
    args = {"core_sha256": "b" * 64, "provider_sha256": "c" * 64}
    assert first.rule_release_id(**args) == second.rule_release_id(**args)
    assert first.rule_release_id(**args) != first.rule_release_id(
        **{**args, "core_sha256": "d" * 64}
    )
    assert first.rule_release_id(**args) != first.rule_release_id(
        **{**args, "provider_sha256": "e" * 64}
    )


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/etc/passwd",
        "C:/secret",
        "a\\b",
        "a//b",
        "a/../b",
        "a\x00b",
        ".",
        "con.txt",
        "ptcg/aux.py",
        "a?b",
    ],
)
def test_unsafe_manifest_paths_rejected_before_reads(assets, path):
    manifest = json.loads((assets[0] / "bundle.json").read_text())
    manifest["components"][0]["path"] = path
    pin = write_manifest(assets[0], manifest)
    with pytest.raises(ProviderError, match="UNSAFE_ASSET_PATH"):
        DirectoryEngineProvider(assets[0], pin).load()


@pytest.mark.parametrize(
    "change,code",
    [
        (
            lambda m: m.update(engineAbi="upstream-without-patches"),
            "UNSUPPORTED_BUNDLE",
        ),
        (lambda m: m.update(schema=True), "UNSUPPORTED_BUNDLE"),
        (lambda m: m["components"].pop(), "INCOMPLETE_BUNDLE"),
        (lambda m: m["components"].append(m["components"][0]), "DUPLICATE_ASSET"),
        (lambda m: m["components"][0].update(role="image"), "INVALID_ASSET_ROLE"),
        (lambda m: m["components"][0].update(size=2**30), "ASSET_TOO_LARGE"),
        (lambda m: m["components"][0].update(sha256="f" * 64), "ASSET_HASH_MISMATCH"),
    ],
)
def test_fail_closed_on_invalid_bundle(assets, change, code):
    manifest = json.loads((assets[0] / "bundle.json").read_text())
    change(manifest)
    pin = write_manifest(assets[0], manifest)
    with pytest.raises(ProviderError, match=code):
        DirectoryEngineProvider(assets[0], pin).load()


def test_manifest_must_be_pinned_by_caller(assets):
    with pytest.raises(ProviderError, match="MANIFEST_MISMATCH"):
        DirectoryEngineProvider(assets[0], "f" * 64).load()
    with pytest.raises(ProviderError, match="INVALID_MANIFEST_PIN"):
        DirectoryEngineProvider(assets[0], "").load()


def test_symlink_files_and_parents_rejected(assets, tmp_path):
    file = assets[0] / "assets/catalog.json"
    target = tmp_path / "elsewhere"
    file.rename(target)
    file.symlink_to(target)
    with pytest.raises(ProviderError, match="SYMLINK_ASSET"):
        load(assets)
    directory = assets[0] / "alias"
    directory.symlink_to(assets[0] / "assets", target_is_directory=True)
    with pytest.raises(ProviderError, match="SYMLINK_ASSET"):
        read_asset(assets[0], "alias/catalog.json")


def test_duplicate_json_and_corrupt_policy_rejected(tmp_path):
    with pytest.raises(ProviderError, match="DUPLICATE_JSON_KEY"):
        parse_json(b'{"a":1,"a":2}')
    with pytest.raises(ProviderError, match="INVALID_JSON"):
        parse_json(b'{"a":1e999}')
    rows = [
        (n, role, b"{}" if role == "format" else content)
        for n, role, content in payloads()
    ]
    pin = write_bundle(tmp_path, rows)
    with pytest.raises(ProviderError, match="INVALID_FORMAT"):
        DirectoryEngineProvider(tmp_path, pin).load()


def test_independent_bundles_and_support_queries(tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    pin1 = write_bundle(one)
    changed = [
        (n, r, canonical({"effects": []}) if r == "effects" else b)
        for n, r, b in payloads()
    ]
    pin2 = write_bundle(two, changed)
    a = DirectoryEngineProvider(one, pin1).load()
    b = DirectoryEngineProvider(two, pin2).load()
    entries = [{"printingId": "synthetic-basic", "quantity": 1}]
    for _ in range(3):
        assert a.effect_support(entries).supported
        assert b.effect_support(entries).missing == ("synthetic-basic",)
    assert a.bundle_id != b.bundle_id


def test_errors_do_not_echo_private_path_or_contents(tmp_path):
    with pytest.raises(ProviderError) as error:
        read_asset(tmp_path, "private-sentinel.json")
    assert str(error.value) == "ASSET_UNAVAILABLE"
    assert "private-sentinel" not in str(error.value)


def test_engine_text_is_snapshotted_without_execution(assets):
    assert b"raise RuntimeError" in load(assets).engine_sources()["ptcg/__init__.py"]
    assert "ptcg" not in sys.modules
