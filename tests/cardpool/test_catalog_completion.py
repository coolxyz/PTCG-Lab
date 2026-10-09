import copy

from scripts.cardpool.catalog_inventory import product_tables
from scripts.cardpool.publish_catalog import augment, proposed_id
from scripts.cardpool.fetch_article_html import parse_html


def test_deck_nicknames_use_cn_series_and_foreign_tables_are_explicit():
    html = """<a href="/wiki/File:SetSymbolCS4DaC.png"></a>
    <h2>卡牌列表</h2><a href="/wiki/File:SetSymbolSIF.png"></a>
    <table><tr><th>编号</th><th>卡牌名称</th></tr>
    <tr><td>001/414</td><td><a title="甲（TCG）">甲</a></td></tr></table>
    <h3>韩文补充表</h3><a href="/wiki/File:SetSymbolSH.png"></a>
    <table><tr><th>编号</th><th>卡牌名称</th></tr>
    <tr><td>001/414</td><td><a title="乙（TCG）">乙</a></td></tr></table>
    <table class="navbox"><tr><td><a href="/wiki/File:SetSymbolUNRELATEDC.png"></a></td></tr></table>"""
    rows, summary = product_tables(html, "初阶牌组100（TCG）")
    assert rows[0]["productCode"] == "CS4DaC" and not rows[0]["foreign"]
    assert rows[1]["foreign"]
    assert summary["codes"] == ["CS4DaC"]


def test_unnumbered_trophies_and_energy_rows_are_retained_without_fake_numbers():
    html = """<h2>没有编号</h2><table><tr><th>编号</th><th>卡牌名称</th><th>获得方式</th></tr>
    <tr><td>SV-P</td><td><a title="奖牌（TCG）">奖牌</a>冠军</td><td>活动甲</td></tr>
    <tr><td>SV-P</td><td><a title="奖牌（TCG）">奖牌</a>亚军</td><td>活动甲</td></tr></table>"""
    rows, _ = product_tables(html, "SV-P简体中文版特典卡（TCG）")
    assert len(rows) == 2 and all(r["collectorNumber"] is None for r in rows)
    assert proposed_id(rows[0]) != proposed_id(rows[1])
    energy = {
        "productCode": "TESTC",
        "printedNumber": "—",
        "collectorNumber": None,
        "basicEnergyType": "grass",
    }
    assert proposed_id(energy) == "CN:basic:GRA"


def test_html_trainer_hidden_hp_placeholder_is_not_pokemon_metadata():
    html = """<h1 id="firstHeading">竞技场（TCG）</h1><a href="?oldid=123">修订</a>
    <div class="mw-parser-output"><p>竞技场（英文︰ Stadium ）是一张 竞技场卡。</p>
    <table><tr><td>TRAINER <span style="display:none">HP 10</span></td></tr></table></div>"""
    parsed = parse_html(html)
    assert parsed["facts"]["category"] == "训练家"
    assert parsed["facts"]["subtype"] == "STADIUM"
    assert "hp" not in parsed["facts"]
    assert parsed["revision"] == 123


def test_html_printing_pair_does_not_borrow_traditional_column():
    def cell(value):
        return "<td>" + value + "</td>"

    values = [
        '<a href="/wiki/File:SetSymbolTESTC.png"></a>',
        "简中产品",
        "系列",
        "2025-01-01",
        "R",
        "001/100",
        "",
        '<a href="/wiki/File:SetSymbolOTHERF.png"></a>',
        "繁中产品",
        "系列",
        "2024-01-01",
        "R",
        "090/100",
        "",
        '<img alt="H">',
    ]
    html = (
        '<h1 id="firstHeading">甲</h1><div class="mw-parser-output"><p>甲是一张基础宝可梦卡。</p><table><tr><td>中文卡包</td></tr><tr>'
        + "".join(map(cell, values))
        + "</tr></table></div>"
    )
    row = parse_html(html)["paired"][0]
    assert row["cnicon"] == "TESTC" and row["cnno"] == "001/100" and row["reg"] == "H"


def test_conflicting_sources_are_separate_and_do_not_gain_support():
    source = {
        "title": "产品",
        "revision": 1,
        "pageId": 1,
        "url": "https://wiki.52poke.com/wiki/test",
    }
    product = {
        "id": "wiki:1",
        "name": "产品",
        "title": "产品",
        "releasedAt": "2025-01-01",
        "source": source,
        "tables": [{"foreign": False, "namedRows": 2}],
    }
    base = {
        "productCode": "TESTC",
        "collectorNumber": "001",
        "printedNumber": "001/001",
        "category": "宝可梦",
        "sourceEvidence": [
            {
                "productId": "wiki:1",
                "product": "产品",
                "source": source,
                "table": 1,
                "row": 2,
                "method": "product-table",
            }
        ],
    }
    rows = [
        {**copy.deepcopy(base), "cardPage": "甲（TCG）", "cnName": "甲"},
        {**copy.deepcopy(base), "cardPage": "乙（TCG）", "cnName": "乙"},
    ]
    rows[1]["sourceEvidence"][0]["row"] = 3
    inventory = {
        "asOf": "2026-10-01",
        "products": [product],
        "records": rows,
        "excluded": [],
    }
    catalog = {"cards": [], "products": [], "version": "old"}
    result, report = augment(catalog, inventory)
    assert len(result["cards"]) == 2
    assert all(c["printingId"].startswith("CN:source:") for c in result["cards"])
    assert all(
        c["effectStatus"] == "unverified" and not c["sourceVerified"]
        for c in result["cards"]
    )
    assert report["mappedCNTableRows"] == report["namedCNTableRows"] == 2
    second, _ = augment(result, inventory)
    assert result == second

