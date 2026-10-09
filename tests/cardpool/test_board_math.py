from ptcg.core.card_registry import registry
from ptcg.core.enums import PokemonRule, CardType
from packages.rules.board_math import value
from tests.cardpool.test_checkup import board
from test_effects import zone


def evaluate(s, p, o, term, **kwargs):
    return value({"term": term, **kwargs}, p.active[0], o.active[0], p, o, s)


def test_all_damaged_bench_requires_nonempty_bench_and_counts_each_pokemon():
    s, p, o = board()
    zone(p, "bench", [])
    assert evaluate(s, p, o, "bench_damaged", all=True) == 0
    cards = [registry.get("P01-005")() for _ in range(2)]
    zone(p, "bench", cards)
    cards[0].hp -= 10
    assert evaluate(s, p, o, "bench_damaged") == 1
    assert evaluate(s, p, o, "bench_damaged", all=True) == 0
    assert evaluate(s, p, o, "field_matching", damaged=True) == 1
    cards[1].hp -= 10
    assert evaluate(s, p, o, "bench_damaged", all=True) == 1


def test_discard_trait_counts_trainers_as_well_as_pokemon_and_physical_energy():
    s, p, o = board()
    from packages.rules.plain import TRAINER_SPECS
    trainer = registry.get(next(c["effectKey"] for c in TRAINER_SPECS if c["name"] == "Professor Sada's Vitality"))()
    trainer.pokemonRule = PokemonRule.ANCIENT
    pokemon = registry.get("P01-005")()
    pokemon.pokemonRule = PokemonRule.ANCIENT
    energy = registry.get("P01-003")()
    zone(p, "discard", [trainer, pokemon, energy])
    assert evaluate(s, p, o, "discard_trait", trait="ANCIENT") == 2
    assert evaluate(s, p, o, "own_discard_energy") == 1


def test_name_contains_scans_both_fields_and_exact_name_does_not():
    s, p, o = board()
    p.active[0].name, o.active[0].name = "Team Rocket's Weezing", "Galarian Weezing"
    p.bench[0].name, o.bench[0].name = "Koffing", "Nidoking"
    assert evaluate(s, p, o, "field_matching", owner="both", contains=["Koffing", "Weezing"]) == 3
    assert evaluate(s, p, o, "field_matching", name="Weezing") == 0
    assert evaluate(s, p, o, "field_matching", prefix="Team Rocket's ") == 1


def test_type_sets_do_not_count_duplicate_types():
    s, p, o = board()
    cards = [registry.get("P01-005")() for _ in range(3)]
    zone(p, "bench", cards)
    for c, t in zip(cards, [CardType.FIRE, CardType.FIRE, CardType.WATER]):
        c.cardType = t
    assert evaluate(s, p, o, "bench_types") == 2
    p.active[0].cardType = CardType.METAL
    for c in o.active + o.bench:
        c.cardType = CardType.GRASS
    assert evaluate(s, p, o, "shared_type") == 0
    o.bench[0].cardType = CardType.WATER
    assert evaluate(s, p, o, "shared_type") == 1


def test_clause_compiler_rejects_additional_text_and_wrong_multiplication():
    from scripts.cardpool.math_clause_rules import CLAUSES, compile_math_clause
    for c in CLAUSES:
        r = c["rule"]
        p = {"eeffect": c["english"], "effectZHS": c["chinese"], "damage": str(r["factor"]), "damageP": {"add":"加","subtract":"减","multiply":"乘"}[r["mode"]]}
        assert compile_math_clause(p) == r
        assert compile_math_clause({**p, "effectZHS": p["effectZHS"] + "然后抽取1张卡牌。"}) is None
        assert compile_math_clause({**p, "eeffect": p["eeffect"] + " Draw a card."}) is None
        if r["mode"] == "multiply":
            assert compile_math_clause({**p, "damage": "999"}) is None


def test_energy_count_coins_are_game_rng_driven_and_count_units():
    import random
    from scripts.cardpool.compile_plain import coin_rule
    from tests.cardpool.test_field_effects import field_fixture
    from test_effects import drive
    from ptcg.core.enums import Coin
    from ptcg.utils.utils import flip_coin
    r = coin_rule({"damage": "80×", "eeffect": "Flip a coin for each {{e|火}} Energy attached to this Pokémon. This attack does 80 damage for each heads.", "effectZHS": "抛掷与这只宝可梦身上附着的{{e|火}}能量数量相同次数的硬币，造成正面次数×80伤害。"})
    assert r["countTerm"] == "self_typed_energy"
    s, p, o, c, t, a = field_fixture(r)
    c.energy = [CardType.FIRE, CardType.ANY, CardType.WATER]
    t.hp, t.weakness, t.resistance = 1000, [], []
    s.rng = random.Random(4)
    expected = sum(flip_coin(s) == Coin.HEAD for _ in range(2)) * 80
    s.rng = random.Random(4)
    drive(c.reduce_action(a, s))
    assert t.hp == 1000 - expected
