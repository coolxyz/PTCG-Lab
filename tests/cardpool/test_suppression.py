from ptcg.core.card_registry import registry
from ptcg.core.enums import PokemonType, PokemonRule, Stage
from packages.rules.suppression import enabled
from tests.cardpool.test_checkup import board
from test_effects import zone


def lock(card, targets, active=False):
    card.spec = {
        "abilities": [
            {"kind": "suppress_abilities", "targets": targets, "activeOnly": active}
        ]
    }


def test_spiritomb_suppresses_both_players_basic_v_including_bench():
    s, p, o = board()
    lock(p.active[0], "basic_v")
    for target in [p.bench[0], o.active[0]]:
        target.pokemonType, target.stage = PokemonType.V, Stage.BASIC
        assert not enabled(target, s)
    o.bench[0].pokemonType, o.bench[0].stage = PokemonType.VSTAR, Stage.STAGE_1
    assert enabled(o.bench[0], s)
    assert enabled(p.active[0], s)


def test_iron_thorns_excludes_future_and_ordinary_ancient():
    s, p, o = board()
    lock(p.active[0], "rule_box_except_future", active=True)
    p.active[0].pokemonType, p.active[0].pokemonRule = (
        PokemonType.EX,
        PokemonRule.FUTURE,
    )
    o.active[0].pokemonType = PokemonType.EX
    assert not enabled(o.active[0], s)
    o.active[0].pokemonRule = PokemonRule.FUTURE
    assert enabled(o.active[0], s)
    o.bench[0].pokemonType, o.bench[0].pokemonRule = (
        PokemonType.NORMAL,
        PokemonRule.ANCIENT,
    )
    assert enabled(o.bench[0], s)


def test_flutter_mane_disables_iron_thorns_and_restores_benched_ex():
    s, p, o = board()
    lock(p.active[0], "rule_box_except_future", active=True)
    p.active[0].pokemonType, p.active[0].pokemonRule = (
        PokemonType.EX,
        PokemonRule.FUTURE,
    )
    o.bench[0].pokemonType = PokemonType.EX
    assert not enabled(o.bench[0], s)
    flutter = registry.get("PRE-043")()
    zone(o, "active", [flutter])
    assert enabled(flutter, s) and not enabled(p.active[0], s)
    assert enabled(o.bench[0], s)


def test_two_flutter_mane_preserve_the_excepted_ability():
    s, p, o = board()
    zone(p, "active", [registry.get("PRE-043")()])
    zone(o, "active", [registry.get("PRE-043")()])
    assert enabled(p.active[0], s) and enabled(o.active[0], s)
