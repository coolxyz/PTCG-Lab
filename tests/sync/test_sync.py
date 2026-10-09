import copy
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from packages.sync.common import SyncError, write, digest
from packages.sync.normalize import normalize, difference
from packages.sync.identity import build_catalog
from packages.sync.adapt import compile_basic, environment_status
from packages.sync.jobs import Jobs
from packages.sync.source import GitSource


@pytest.fixture
def raw():
    return {"dict": {"card_type": [{"dictCode": "1", "dictValue": "宝可梦"}], "attribute": [{"dictCode": "1", "dictValue": "草"}], "ability_cost": [{"dictCode": "11", "dictValue": "无色"}], "weakness_type": [], "resistance_type": []}, "collections": [{"id": 1, "name": "测试包", "commodityCode": "TEST", "salesDate": "2026-10-01", "cards": [{"id": 1, "name": "测试宝可梦", "image": "img/1/0.png", "details": {"id": 1, "cardName": "测试宝可梦", "collectionNumber": "001/010", "cardType": "1", "regulationMarkText": "J", "hp": 90, "attribute": "1", "evolveText": "基础", "retreatCost": 1, "rarityText": "C", "special_shiny_type": 0, "abilityItemList": [{"abilityName": "撞击", "abilityText": "", "abilityCost": "11", "abilityDamage": "20"}]}}]}]}


def test_faces_keep_leading_zero_and_costs(raw):
    n = normalize(raw)
    c = n["cards"]["1"]
    assert c["number"] == "001/010"
    assert c["face"]["attacks"][0]["cost"] == ["COLORLESS"]
    assert compile_basic(c["face"])["attacks"][0]["damage"] == 20


def test_unknown_rule_and_dictionary_fail_closed(raw):
    d = raw["collections"][0]["cards"][0]["details"]
    d["abilityItemList"][0]["abilityText"] = "对手获胜。"
    assert compile_basic(normalize(raw)["cards"]["1"]["face"]) is None
    d["abilityItemList"][0]["abilityText"] = ""
    d["abilityItemList"][0]["abilityCost"] = "999"
    n = normalize(raw)
    assert "UNKNOWN_ABILITY_COST" in n["cards"]["1"]["issues"]
    assert compile_basic(n["cards"]["1"]["face"]) is None


def test_changes_include_image_only_old_product_and_semantics(raw):
    old = normalize(raw, {"img/1/0.png": "a"})
    newer = normalize(raw, {"img/1/0.png": "b"})
    assert difference(old, newer)["imageChanged"] == ["1"]
    assert difference(old, newer)["ruleChanged"] == []
    raw["collections"][0]["cards"][0]["details"]["newRuleFlag"] = "yes"
    assert difference(old, normalize(raw))["ruleChanged"] == ["1"]


def test_duplicate_id_conflict_preserved(raw):
    other = copy.deepcopy(raw["collections"][0]["cards"][0])
    other["details"]["hp"] = 200
    raw["collections"][0]["cards"].append(other)
    n = normalize(raw)
    assert n["problems"][0]["code"] == "DUPLICATE_CARD_CONFLICT"
    assert "DUPLICATE_CARD_CONFLICT" in n["cards"]["1"]["issues"]


def test_finishes_share_printing_without_duplicate_inventory(raw):
    other = copy.deepcopy(raw["collections"][0]["cards"][0])
    other["id"] = other["details"]["id"] = 2
    other["details"]["special_shiny_type"] = 1
    other["details"]["rarityText"] = "C★"
    raw["collections"][0]["cards"].append(other)
    baseline = {"cards": [], "products": [], "templates": [], "version": "old"}
    migration = build_catalog(normalize(raw), baseline, {"cards": {}}, "a" * 40)
    assert len(migration["catalog"]["cards"]) == 1
    assert len(migration["catalog"]["cards"][0]["variants"]) == 2
    repeated = build_catalog(normalize(raw), migration["catalog"], migration["details"], "a" * 40, previous=migration)
    assert len(repeated["catalog"]["cards"]) == 1
    assert len(repeated["catalog"]["cards"][0]["variants"]) == 2
    assert migration["catalog"]["version"] == repeated["catalog"]["version"]


def test_migration_retains_local_id_and_removed_reference(raw):
    n = normalize(raw)
    first = build_catalog(n, {"cards": [], "products": [], "templates": []}, {"cards": {}}, "a" * 40)
    card = first["catalog"]["cards"][0]
    card["printingId"] = "local-original"
    next_ = build_catalog(n, first["catalog"], first["details"], "b" * 40)
    assert next_["mappings"]["1"]["printingId"] == "local-original"
    n["cards"] = {}
    n["occurrences"] = []
    removed = build_catalog(n, next_["catalog"], next_["details"], "c" * 40, previous=next_)
    assert removed["catalog"]["cards"][0]["upstreamRemoved"]


def test_revision_updates_source_owned_facts_and_revokes_old_effect(raw):
    first = build_catalog(normalize(raw), {"cards": [], "products": [], "templates": []}, {"cards": {}}, "a" * 40)
    old = first["catalog"]["cards"][0]
    old.update(effectStatus="verified", sourceVerified=True)
    raw["collections"][0]["cards"][0]["details"]["hp"] = 120
    raw["collections"][0]["cards"][0]["details"]["regulationMarkText"] = "H"
    raw["collections"][0]["salesDate"] = "2026-09-01"
    revised = build_catalog(normalize(raw), first["catalog"], first["details"], "b" * 40, previous=first)
    card = revised["catalog"]["cards"][0]
    assert card["printingId"] == old["printingId"]
    assert (card["hp"], card["mark"], card["releasedAt"]) == (120, "H", "2026-09-01")
    assert card["effectStatus"] == "unverified" and not card["sourceVerified"]


@pytest.mark.parametrize("path", ["../secret", "/tmp/x", "img/../../x", "C:/x", "img\\x"])
def test_paths_cannot_escape(path):
    with pytest.raises(SyncError):
        GitSource.validate_path(path)


def test_job_idempotency_cancellation_and_cross_instance_lease(tmp_path):
    a, b = Jobs(tmp_path), Jobs(tmp_path)
    first = a.create("same-input", {"commit": "a"})
    assert b.create("same-input", {})["id"] == first["id"]
    with a.lease():
        with pytest.raises(SyncError, match="已有同步"):
            with b.lease():
                pass
    with b.lease():
        b.update(first["id"], stage="fetching", state="running")
    a.cancel(first["id"])
    assert b.get(first["id"])["cancelled"]
    assert b.events(first["id"])[0]["stage"] == "fetching"


def test_environment_separates_release_scope_and_policy():
    rules = {"allowedMarks": ["J"]}
    scope = {"allowedMarks": ["G", "J"], "basicEnergyTypes": ["grass"]}
    c = {"printingId": "x", "releasedAt": "2026-10-10", "mark": "J"}
    assert environment_status(c, rules, scope, "2026-10-07") == "not-released"
    c.update(releasedAt="2026-10-01", mark="G")
    assert environment_status(c, rules, scope, "2026-10-07") == "outside-format"


def test_local_git_reads_fixed_commit_not_mutable_head(tmp_path, raw):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=upstream).decode().strip()
    git("init", "-b", "main")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Sync fixture")
    write(upstream / "ptcg_chs_infos.json", raw)
    git("add", ".")
    git("commit", "-m", "fixture")
    source = GitSource(tmp_path / "cache", str(upstream), allow_local=True)
    first = source.fetch()
    raw["collections"][0]["name"] = "changed"
    write(upstream / "ptcg_chs_infos.json", raw)
    git("add", ".")
    git("commit", "-m", "change")
    second = source.fetch()
    assert first != second and source.is_ancestor(first, second)
    assert json.loads(source.blob(first, "ptcg_chs_infos.json"))["collections"][0]["name"] == "测试包"
    cached = source.cache_all(first)
    assert cached["missing"] == 0 and cached["files"] == 1
    # Once cached, reading the pinned source does not depend on its remote.
    source.git("remote", "set-url", "origin", str(tmp_path / "offline"))
    assert json.loads(source.blob(first, "ptcg_chs_infos.json"))["collections"][0]["name"] == "测试包"
