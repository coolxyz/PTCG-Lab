"""Validate pinned Git objects and exact printing-to-image matching."""

from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess

from PIL import Image
import pytest
from scripts.product import git_artwork as tool


@pytest.fixture
def example(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)], check=True, capture_output=True
    )
    image = io.BytesIO()
    Image.new("RGB", (6, 8), (70, 20, 100)).save(image, format="PNG")
    (repo / "img").mkdir()
    (repo / "img/one.png").write_bytes(image.getvalue())
    details = {
        "id": 123,
        "cardName": "测试牌",
        "collectionNumber": "001/060",
        "cardType": "POKEMON",
        "hp": 70,
        "special_shiny_type": 0,
    }
    raw = {
        "collections": [
            {
                "id": 1,
                "commodityCode": "TEST",
                "cards": [
                    {
                        "id": 123,
                        "name": "测试牌",
                        "image": "img/one.png",
                        "details": details,
                    }
                ],
            }
        ]
    }
    (repo / "ptcg_chs_infos.json").write_text(json.dumps(raw))
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "synthetic artwork",
        ],
        check=True,
        capture_output=True,
    )
    cache = tool.GitCache(tmp_path / "cache", str(repo), allow_local=True)
    commit = cache.fetch()
    tree = cache.tree(commit)
    catalog = {
        "asOf": "2026-09-30",
        "formatId": "synthetic",
        "cards": [
            {
                "printingId": "p1",
                "engineId": "e1",
                "cnName": "测试牌",
                "collectorNumber": "001",
                "productCode": "TEST",
                "hp": 70,
                "variants": [{"upstreamId": "123"}],
            }
        ],
    }
    from packages.sync.normalize import COSMETIC
    contract = (None,None,None,None,COSMETIC)
    return cache, commit, tree, catalog, raw, contract




def test_identity_mismatch_and_conflicting_face_are_not_guessed(example):
    _, _, tree, catalog, raw, contract = example
    wrong = deepcopy(raw)
    wrong["collections"][0]["cards"][0]["details"]["cardName"] = "另一张牌"
    wrong["collections"][0]["cards"][0]["name"] = "另一张牌"
    assert not tool.plan(catalog, wrong, tree, cosmetic=contract[4])["selected"]
    ambiguous = deepcopy(raw)
    row = deepcopy(ambiguous["collections"][0]["cards"][0])
    row["id"] = row["details"]["id"] = 124
    row["details"]["ruleText"] = "different rules"
    ambiguous["collections"][0]["cards"].append(row)
    catalog["cards"][0]["variants"] = []
    result = tool.plan(catalog, ambiguous, tree, cosmetic=contract[4])
    assert result["skipped"][0]["reason"] == "ambiguous-printed-face"
    with pytest.raises(ValueError, match="do not exist"):
        tool.plan(catalog, raw, tree, printing_ids=["new-synced-id"])


def test_printed_total_mismatch_is_not_silently_matched(example):
    _, _, tree, catalog, raw, contract = example
    catalog["cards"][0]["printedNumber"] = "001/100"
    assert not tool.plan(catalog, raw, tree, cosmetic=contract[4])["selected"]
    catalog["cards"][0]["printedNumber"] = "001/060"
    assert len(tool.plan(catalog, raw, tree, cosmetic=contract[4])["selected"]) == 1




def test_fixed_cache_can_be_reused_without_fetch(example, monkeypatch):
    cache, commit, tree, _, _, _ = example
    original = cache.git

    def offline(*args, **kwargs):
        assert "fetch" not in args
        return original(*args, **kwargs)

    monkeypatch.setattr(cache, "git", offline)
    assert cache.fetch(commit) == commit
    assert cache.blob(tree["img/one.png"], 8192)


def test_cache_origin_and_paths_are_checked(example, tmp_path):
    cache, _, _, _, _, _ = example
    cache.git("remote", "set-url", "origin", "https://github.com/other/Repo")
    with pytest.raises(ValueError, match="origin mismatch"):
        cache.initialize()
    for name in ["../img.png", "img//x.png", "/etc/passwd", "C:\\file", "x\n.png"]:
        with pytest.raises(ValueError):
            tool.regular_path(name)
    for url in [
        "https://user:secret@github.com/a/b",
        "file:///repo",
        "https://evil.example/a/b",
    ]:
        with pytest.raises(ValueError):
            tool.GitCache(tmp_path / "x", url)
