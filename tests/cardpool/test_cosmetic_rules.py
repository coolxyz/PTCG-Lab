from scripts.cardpool.cosmetic_rules import compile_cosmetic, key


def test_markup_and_type_aliases_preserve_the_complete_clause():
    assert key("'''{{TCG|睡眠}}'''，{{e|火}}") == key("【睡眠】{{e|Fire}}")
    assert key("[[Pokémon Checkup]]") == key("Pokémon Checkup")
    assert key("Basic {{e|Fire}} Energy") != key("Basic {{e|Water}} Energy")
    assert key("up to 2 cards") != key("up to 3 cards")


def test_cosmetic_attack_match_still_requires_both_languages_and_correct_operators():
    from scripts.cardpool.math_clause_rules import CLAUSES
    c=CLAUSES[0]
    p={"eeffect":c["english"],"effectZHS":c["chinese"].replace("，",", "),"damage":"10+"}
    assert compile_cosmetic(p)==c["rule"]
    assert compile_cosmetic({**p,"damage":"10"}) is None
    assert compile_cosmetic({**p,"effectZHS":p["effectZHS"]+"再抽1张卡牌。"}) is None
    assert compile_cosmetic({**p,"eeffect":p["eeffect"].replace("exactly 1","exactly 2")}) is None
