from scripts.cardpool.targets import tables
import hashlib
import json


def test_promos_and_precons_are_enumerated_without_using_index_symbol_guess():
    html = """<a href="/wiki/File:SetSymbolWRONG.png"></a>
    <table><tr><th>编号</th><th>卡牌名称</th><th>获得方式</th></tr>
    <tr><td>001/SV-P</td><td><a title="甲（SV1）">甲</a></td><td>活动</td></tr></table>
    <a href="/wiki/File:SetSymbolCSVM2cC.png"></a>
    <table><tr><th>编号</th><th>卡牌名称</th><th>数量</th></tr>
    <tr><td>003/032</td><td><a title="乙（SV2）">乙</a></td><td>1</td></tr>
    <tr><td>随机组合</td><td>说明</td><td></td></tr></table>"""
    parsed = tables(html)
    assert len(parsed) == 2
    assert parsed[0]["rows"][0]["productCode"] == "SV-P"
    assert parsed[1]["rows"][0]["productCode"] == "CSVM2cC"
    assert parsed[1]["rows"][0]["collectorNumber"] == "003"
    assert len(parsed[1]["skipped"]) == 1


def test_rowspan_and_blank_span_preserve_card_identity_and_source_location():
    html = """<a href="/wiki/File:SetSymbolCSV1C.png"></a>
    <table><tr><th>编号</th><th>卡牌名称</th><th>备注</th></tr>
    <tr><td>001/001</td><td rowspan="2"><a title="同一规则页">甲</a></td><td></td></tr>
    <tr><td colspan="">002/001</td><td></td></tr></table>"""
    rows = tables(html)[0]["rows"]
    assert len(rows) == 2
    assert rows[0]["cardPage"] == rows[1]["cardPage"] == "同一规则页"
    assert [r["sourceRow"] for r in rows] == [2, 3]


def test_rebuild_keeps_source_dates_instead_of_reusing_derived_release(
    tmp_path, monkeypatch
):
    from scripts.cardpool import targets

    (tmp_path / "rulesets").mkdir()
    (tmp_path / "data/catalog").mkdir(parents=True)
    (tmp_path / "rulesets/cn-standard-2026-09-16.json").write_text(
        json.dumps(
            {
                "id": "test",
                "allowedMarks": ["G", "H"],
                "basicEnergyTypes": [],
                "legacyReprintNames": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "data/catalog/catalog.json").write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "printingId": "CN:TESTC:001",
                        "sourceVerified": True,
                        "reviewScope": "p4-equivalent-reprint-v1",
                        "mark": "H",
                        "releasedAt": "2026-01-01",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "data/catalog/number-overrides.json").write_text(
        '{"corrections": []}', encoding="utf-8"
    )
    raw = json.dumps(
        {
            "parse": {
                "text": {
                    "*": '<a href="/wiki/File:SetSymbolTESTC.png"></a><table><tr><th>编号</th><th>卡牌名称</th></tr><tr><td>001/001</td><td><a title="Card">甲</a></td></tr></table>'
                }
            }
        }
    ).encode()
    (tmp_path / "source.json").write_bytes(raw)
    source = {
        "asOf": "2026-10-01",
        "products": [
            {
                "title": "Test",
                "metadata": {"发布时间": "2025年1月1日"},
                "source": {
                    "cachePath": "source.json",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "revision": 1,
                },
            }
        ],
    }
    monkeypatch.setattr(targets, "ROOT", tmp_path)
    pages = {
        "Card": {
            "title": "Card",
            "text": "{{ExpansionList/main/zh|cnicon=TESTC|cnno=001/001|reg=G}}",
        }
    }
    result = targets.build(source, pages)["cards"][0]
    assert result["releasedBy"] == "2025-01-01"
    assert result["sources"][0]["releaseBound"] == "2025-01-01"
    assert result["mark"] == "G"
