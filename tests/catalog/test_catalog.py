import json, sqlite3
from pathlib import Path
from bs4 import BeautifulSoup
from scripts.catalog.parse_sets import table_grid, parse_tables, release_dates
from scripts.catalog.enrich import parse_page, number
from scripts.catalog.artwork import origins, match_detail, set_key
from packages.collection.domain import CARDS, RAW, validation, analysis, parse_import
from packages.engine_adapter.cn_format import validate

ROOT = Path(__file__).resolve().parents[2]


def test_rowspan_expands_without_losing_variant_numbers():
    html = (
        '<table><tr><td rowspan="2">A</td><td>01</td></tr><tr><td>02</td></tr></table>'
    )
    rows = table_grid(BeautifulSoup(html, "html.parser").table)
    assert [[c.text for c in r] for r in rows] == [["A", "01"], ["A", "02"]]


def test_cn_tables_only_and_red_link_cards():
    html = '<a href="/wiki/File:SetSymbolCSV1C.png"></a><table><tr><th>编号</th><th>卡牌名称</th><th>属性</th></tr><tr><td>001/100</td><td><a title="新卡（S1）（页面不存在）" href="/w/index.php?x">新卡</a></td><td>Su</td></tr></table><a href="/wiki/File:SetSymbolSV1F.png"></a><table><tr><th>编号</th><th>卡牌名称</th><th>属性</th></tr><tr><td>001/100</td><td>foreign</td><td>I</td></tr></table>'
    ts = parse_tables(html, {})
    assert len(ts) == 1 and ts[0]["code"] == "CSV1C"
    c = ts[0]["records"][0]
    assert c["cardPage"] == "新卡（S1）" and c["subtype"] == "SUPPORTER"


def test_jewel_and_anniversary_numbers_are_literal():
    assert number("01 02/07") == "01-02"
    assert number("R/RGB") == "R"
    assert number("017/103 01/30") == "017"
    assert release_dates("2026年9月16日、2027年1月1日") == ["2026-09-16", "2027-01-01"]


def test_paired_image_rowspan_expires():
    text = """{{卡牌信息/header/GX|hp=200|evostage=1階進化|type=龍|evo=青綿鳥}}
    {{ExpansionList/header/zh|龙}}
    {{ExpansionList/main/zh|cnicon=A|cnno=001/100|zhicon=SV1F|zhno=010/100|zhimg=x.png|zhimgrow=2}}
    {{ExpansionList/main/zh|cnicon=B|cnno=001/100|zhimg=n}}
    {{ExpansionList/main/zh|cnicon=C|cnno=001/100|zhimg=n}}"""
    facts, rows = parse_page(text)
    assert (
        facts["stage"] == "STAGE_1"
        and facts["hp"] == 200
        and facts["evolvesFrom"] == ["青綿鳥"]
    )
    assert rows[1]["images"][0]["printedNumber"] == "010/100"
    assert rows[2]["images"] == []


def test_official_source_allowlist_and_identity():
    assert origins(
        "[https://evil.example/detail/1 x] https://asia.pokemon-card.com/hk/card-search/detail/8371"
    ) == ["https://asia.pokemon-card.com/hk/card-search/detail/8371"]
    candidate = {"setCode": "SV1VF", "printedNumber": "001/078"}
    detail = {"setCode": "sv1v_f", "printedNumber": "001/078", "hp": 60}
    assert match_detail(candidate, {"hp": 60}, detail)
    assert not match_detail(candidate, {"hp": 70}, detail)
    assert not match_detail(
        candidate, {"hp": 60}, {**detail, "printedNumber": "002/078"}
    )
    assert not match_detail(candidate, {"hp": 60}, {**detail, "setCode": "sv1s_f"})


def test_catalogue_completeness_and_quarantine():
    src = json.loads((ROOT / "tests/fixtures/wiki-card-tables.json").read_text(encoding='utf8'))
    products = [p for p in src["products"] if p["status"] == "released"]
    assert len(products) == 61
    assert len(src["sourceRows"]) == sum(p["tableCount"] for p in products)
    assert not any(p.get("unparsedRows") for p in products)
    assert not any(p["status"] == "source_error" for p in src["products"])
    assert len(src["cards"]) == len({c["printingId"] for c in src["cards"]}) == 8239
    assert not src["conflicts"]
    assert len(src["corrections"]) == 3
    assert all(pid not in CARDS for pid in src["conflicts"])
    assert CARDS['CN:151C:001']['upstreamFaces']
    assert all(p["releasedAt"] <= src["asOf"] for p in products)


def test_new_cards_are_usable_drafts_but_not_certified():
    # This B-mark printing remains outside the implemented GHIJ battle pool.
    card = CARDS["CN:CSM1aC:001"]
    assert (
        card["cnName"] == "飞天螳螂"
        and card["hp"] == 70
        and card["isBasicPokemon"] is True
    )
    assert card["effectStatus"] == "unverified" and card["sourceVerified"] is False
    result = validation([{"printingId": card["printingId"], "quantity": 4}])
    assert not result["playable"] and result["engine"] == "unverified"
    assert "EFFECT_NOT_VERIFIED" in {i["code"] for i in result["issues"]}
    assert parse_import("4 飞天螳螂 CSM1aC 001")["entries"] == [
        {"printingId": card["printingId"], "quantity": 4}
    ]
    assert sum(c["effectStatus"] == "verified" for c in CARDS.values()) >= 164


def test_unknown_stage_does_not_claim_zero_basic_probability():
    card = next(
        c
        for c in CARDS.values()
        if c["category"] == "宝可梦" and c["isBasicPokemon"] is None
    )
    es = [{"printingId": card["printingId"], "quantity": 7}]
    assert analysis(es)["openingBasicProbability"] is None
    codes = {
        i["code"] for i in validate([{"printing": card, "quantity": 60}])["issues"]
    }
    assert "BASIC_STATUS_UNKNOWN" in codes and "NO_BASIC_POKEMON" not in codes


def test_sqlite_relations_and_preserved_source_rows():
    con = sqlite3.connect(ROOT / "data/catalog/catalog.sqlite")
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == len(CARDS)
    assert all(c.get('upstreamFaces') for c in CARDS.values())
    con.close()


def test_baseline_certification_is_unchanged():
    from packages.simulation.registry import RELEASE
    effects={e['effectKey']:e for e in RELEASE['effects']}
    for current in CARDS.values():
        if current['sourceVerified'] and current['effectStatus']=='verified':
            assert current['printingId'] in effects[current['engineId']]['printings']
    for t in RAW["templates"]:
        assert validation(t["entries"])["playable"]


def test_official_symbol_export_affixes_do_not_merge_different_sets():
    assert set_key("SM_expantion_mark_ac2b") == set_key("AC2b")
    assert set_key("S_mark_expantion_sc2A_F") == set_key("SC2aF")
    assert set_key("SV7_twhk_exp") == set_key("SV7F")
    assert set_key("S11_F@4x") == set_key("S11F")
    assert set_key("SC2bF") != set_key("S_mark_expantion_sc2D_F")


def test_article_facts_resolve_missing_table_category_and_basic_stage():
    energy = CARDS["CN:CS6aC:168"]
    assert energy["category"] == "能量"
    assert energy["basicEnergyType"] == "grass"
    assert CARDS["CN:CSM1.5C:084"]["category"] == "训练家"
    assert CARDS["CN:CSV1C:006"]["isBasicPokemon"] is True


def test_manual_number_corrections_preserve_raw_and_refuse_drift():
    import pytest
    from scripts.catalog.corrections import apply_number_correction

    src = json.loads((ROOT / "tests/fixtures/wiki-card-tables.json").read_text(encoding='utf8'))
    expected = {
        "CN:CSVL1C:068": "花岩怪",
        "CN:CSVL1C:069": "钥圈儿",
        "CN:CSVL2C:068": "轻身鳕",
        "CN:CSVL2C:069": "吃吼霸",
        "CN:CSV9C:136": "铝钢龙",
        "CN:CSV9C:137": "铝钢桥龙",
    }
    for pid, name in expected.items():
        assert CARDS[pid]["cnName"] == name
        # Number review alone did not certify effects. These three corrected
        # printings now have a separately published, revision-bound rule review.
        assert CARDS[pid]['upstreamFaces']
    for c in src["corrections"]:
        raw = next(
            r
            for r in src["sourceRows"]
            if r["cardPage"] == c["cardPage"]
            and r["sourceRow"] == c["sourceRow"]
            and r["printingId"] == f"CN:{c['productCode']}:{c['oldNumber']}"
        )
        assert raw["collectorNumber"] == c["oldNumber"]
        assert (
            apply_number_correction(raw, c["productCode"], c["sourceRevision"], [c])[
                "collectorNumber"
            ]
            == c["newNumber"]
        )
        with pytest.raises(ValueError, match="source changed"):
            apply_number_correction(raw, c["productCode"], c["sourceRevision"] + 1, [c])
