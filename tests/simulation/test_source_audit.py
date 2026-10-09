from scripts.simulation.audit_sources import parse


def test_promo_and_precon_rows_are_kept_without_inventing_identity():
    product = {"url": "https://example.com/product", "source": {"revision": 1}}
    html = """<table><tr><th>编号</th><th>卡牌名称</th><th>数量</th></tr>
    <tr><td>001/SV-P</td><td><a title="甲" href="/a">甲</a></td><td>2</td></tr>
    <tr><td>随机组合</td><td>说明</td><td></td></tr></table>
    <table><tr><th>编号</th><th>卡牌名称</th></tr>
    <tr><td>001/032</td><td><a title="乙" href="/b">乙</a></td></tr></table>"""
    result = parse(html, product)
    assert result["tables"] == 2 and len(result["rows"]) == 2
    assert len(result["skipped"]) == 1
    assert {r["number"] for r in result["rows"]} == {"001/SV-P", "001/032"}
    assert all(
        r["status"] == "identity-and-format-review-required" for r in result["rows"]
    )
