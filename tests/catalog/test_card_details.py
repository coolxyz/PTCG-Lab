import io
from PIL import Image
from fastapi.testclient import TestClient
from apps.api.main import create_app
from scripts.catalog.complete_details import description, text, traditional_candidates

PID = "CN:CSVM2cC:007"


def test_official_listing_membership_preserves_shared_symbol_and_all_pages(monkeypatch):
    from scripts.catalog import official_search as search
    from scripts.catalog.artwork import match_detail
    captured = {}
    def fetch(url):
        page = "2" if "pageNo=2" in url else "1"
        return (f'共 2 頁 <a href="/hk/card-search/detail/{page}/">卡牌</a>'.encode(), None)
    monkeypatch.setattr(search, "fetch", fetch)
    monkeypatch.setattr(search, "read", lambda _: {})
    monkeypatch.setattr(search, "save", lambda path, data: captured.update(data))
    monkeypatch.setattr(search, "official_detail", lambda url: {"source": url, "setCode": "SVO_ex", "printedNumber": "005/021", "hp": 70})
    search.import_expansion("SVOM")
    assert len(captured["details"]) == 2
    for detail in captured["details"].values():
        assert detail["symbolSetCode"] == "SVO_ex"
        assert "expansionCodes=SVOM" in detail["listingSource"]
        assert match_detail({"setCode": "SVOMF", "printedNumber": "005/021"}, {"hp": 70}, detail)
        assert not match_detail({"setCode": "SVODF", "printedNumber": "005/021"}, {"hp": 70}, detail)


def test_chinese_effect_keeps_inline_card_names_and_line_breaks():
    sections = description("{{卡牌信息/attack|ZHSname=招式|cost=無|damage=30|effectZHS=选择{{TCGPM|N的宝可梦}}。<br>抽1张牌。|eeffect=ignored}}")
    assert sections[0]["text"] == "选择N的宝可梦。\n抽1张牌。"
    assert sections[0]["cost"] == ["COLORLESS"]
    assert sections[0]["damage"] == "30"
    assert not sections[0].get("missingText")
    assert text("[[宝可梦|卡牌]]") == "卡牌"
    assert text("宝可梦{{ex}}、宝可梦{{V|MAX}}、{{C|飞腿郎|SM9}}、{{e|火}}能量") == "宝可梦ex、宝可梦VMAX、飞腿郎、[火]能量"
    assert text("-{zh-hans:回复HP。; zh-hant:恢復HP。}-") == "回复HP。"


def test_missing_translation_is_not_silently_marked_complete():
    s = description("{{卡牌信息/power|name=特性|eeffect=Draw a card.}}")
    assert s[0]["missingText"]
    assert s[0]["text"] == ""


def test_template_added_once_per_game_rule_is_not_lost():
    s = description("{{卡牌信息/header/VSTAR|name=阿尔宙斯}}{{卡牌信息/power/VSTAR|ZHSname=星耀诞生|effectZHS=从牌库选择2张卡。}}{{卡牌信息/ssend|class=VSTAR}}")
    assert any("只能使用1次 VSTAR" in item["text"] for item in s)
    assert any("拿取2张奖赏卡" in item["text"] for item in s)
    assert not any("拿取3张" in item["text"] for item in s)
    gx = description("{{卡牌信息/attack/GX|name=闪焰冲锋|damage=300|ceffect=　|eeffect=&ensp;}}")
    assert gx[0]["kind"] == "GX招式" and not gx[0].get("missingText")


def test_wiki_printing_without_image_file_still_has_official_lookup_identity():
    candidates = traditional_candidates([{"images": [], "zhicon": "SV6", "zhno": "081/101"}])
    assert candidates[0]["setCode"] == "SV6"
    assert candidates[0]["printedNumber"] == "081/101"


def test_overview_effect_does_not_mix_in_pocket_rules():
    sections = description("博士的研究效果为'''“丢弃手牌，抽7张卡”。'''\n== PTCG Pocket中 ==\n效果为'''“抽2张卡”。'''")
    assert sections[0]["text"] == "丢弃手牌，抽7张卡"
    assert not description("== PTCG Pocket中 ==\n效果为'''“抽2张卡”。'''")


def test_official_tera_description_is_a_rule_not_an_ability(tmp_path, monkeypatch):
    import scripts.catalog.complete_details as module
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "card.html").write_text('<section class="cardInformationColumn"><div><h3>招式</h3><div class="skill"><span class="skillName">[太晶]</span><p class="skillEffect">備戰區不受招式傷害。</p></div></div></section>', encoding="utf-8")
    result = module.official_sections({"source": "fixture-tera", "cachePath": "card.html"})
    assert result[0]["kind"] == "规则"


def test_missing_image_export_reports_only_unbound_cards(tmp_path):
    client = TestClient(create_app(tmp_path.resolve() / "app.sqlite"))
    client.app.state.card_media.artwork.rows = {}
    client.app.state.sync.images.builtin.rows = {}
    cards = client.get("/api/cards").json()["cards"]
    expected = [c for c in cards if not c["image"].get("url")]
    response = client.get("/api/card-image-gaps")
    assert response.status_code == 200
    assert expected and expected[0]["printingId"] in response.text
