from types import SimpleNamespace
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType
from ptcg.core.reducer import _calculate_damage
from packages.rules.protection import blocked
from packages.rules.modifiers import refresh_costs, attack_damage
from packages.rules.attack_restrictions import allowed
from tests.cardpool.test_shared_abilities import board
from test_effects import zone


def test_extra_prize_requires_damage_knockout_and_survives_discard_reset():
    from tests.cardpool.test_field_effects import field_fixture
    from test_effects import drive

    for hp, expected in [(10, 2), (500, 0)]:
        s, p, o, c, t, a = field_fixture({"kind": "attack_extra_prize", "count": 1})
        t.hp = hp
        t.weakness = t.resistance = []
        zone(o, "bench", [registry.get("P01-005")()])
        a.attack.damage = 50
        drive(c.reduce_action(a, s))
        assert len(p.prize) == 6 - expected
        if expected:
            assert t in o.discard and not hasattr(t, "knockout_bonus_prizes")


def test_optional_draw_can_be_declined():
    from packages.rules.zone_effects import resolve
    from test_effects import drive

    s, p, o, c = board({"kind": "draw", "count": 1})
    before = list(p.hand)
    drive(
        resolve(
            {"kind": "draw_until", "count": 6, "optional": True},
            SimpleNamespace(source=c),
            s,
        ),
        lambda actions, info, turn: actions[0],
    )
    assert p.hand == before


def test_target_damage_bypasses_tera_and_weakness():
    from tests.cardpool.test_field_effects import field_fixture
    from ptcg.core.enums import PokemonRule
    from test_effects import drive

    s, p, o, c, t, a = field_fixture(
        {
            "kind": "target_damage",
            "count": 2,
            "amount": 50,
            "zone": "all",
            "ignoreEffects": True,
            "ignoreWeaknessResistance": True,
        }
    )
    bench = registry.get("P01-005")()
    zone(o, "bench", [bench])
    for target in (t, bench):
        target.hp = 200
        target.weakness = [c.cardType]
        target.damage_shield = {"turn": s.turn_number, "amount": 100}
        target.pokemonRule = PokemonRule.TERA
    drive(c.reduce_action(a, s))
    assert t.hp == bench.hp == 150


def test_blood_moon_cost_tracks_prizes_and_retains_colored_cost():
    s, p, o, c = board(
        {
            "kind": "continuous",
            "trigger": "passive",
            "scope": "self",
            "attackName": "Blood Moon",
            "attackLessPerOpponentPrize": 1,
        }
    )
    # Give the fixture a printed attack that has both colored and Colorless cost.
    c.spec = {
        **c.spec,
        "attacks": [
            {
                **c.spec["attacks"][0],
                "name": "Blood Moon",
                "cost": ["FIRE", "COLORLESS", "COLORLESS"],
            }
        ],
    }
    type(c).spec = c.spec
    c.attacks = type(c)().attacks
    o.prize = o.prize[:4]
    refresh_costs(c, s)
    assert c.attacks[0].cost == [CardType.FIRE]
    o.prize = [object()] * 6
    refresh_costs(c, s)
    assert c.attacks[0].cost == [CardType.FIRE, CardType.COLORLESS, CardType.COLORLESS]


def test_protection_against_abilities_uses_current_suppression():
    s, p, o, c = board(
        {
            "kind": "protection",
            "trigger": "passive",
            "damage": True,
            "source": "ability",
        }
    )
    attacker = o.active[0]
    attacker.ability = [SimpleNamespace()]
    assert blocked(c, s, "damage", attacker)
    assert not blocked(c, s, "effects", attacker)
    c.ability = [SimpleNamespace(suppresses_opponent_active_abilities=True)]
    assert not blocked(c, s, "damage", attacker)


def test_energy_protection_aura_covers_bench_without_clearing_existing_effects():
    s, p, o, c = board(
        {
            "kind": "protection",
            "trigger": "passive",
            "effects": True,
            "scope": "team",
            "hasEnergy": True,
        }
    )
    target = p.bench[0]
    target.poisoned = True
    assert not blocked(target, s, "effects", o.active[0])
    target.energy = [CardType.FIRE]
    assert blocked(target, s, "effects", o.active[0])
    assert not blocked(target, s, "damage", o.active[0])
    assert target.poisoned


def test_passive_damage_bypass_retains_weakness_but_not_target_shield():
    s, p, o, c = board(
        {
            "kind": "continuous",
            "trigger": "passive",
            "scope": "self",
            "ignoreTargetEffects": True,
        }
    )
    target = o.active[0]
    target.weakness = [c.cardType]
    target.resistance = []
    target.damage_shield = {"turn": s.turn_number, "amount": 80}
    assert _calculate_damage(c, target, 50, s) == 100


def test_opponent_active_damage_reduction_precedes_weakness_and_includes_bench():
    s, p, o, c = board(
        {
            "kind": "continuous",
            "trigger": "passive",
            "scope": "opponent",
            "holderZone": "active",
            "affectedZone": "active",
            "damage": -20,
            "allTargets": True,
        }
    )
    attacker = o.active[0]
    c.weakness, c.resistance = [attacker.cardType], []
    assert _calculate_damage(attacker, c, 50, s) == 60
    assert attack_damage(attacker, p.bench[0], 50, s) == 30
    assert attack_damage(attacker, c, 10, s) == 0


def test_attack_population_requirement_rechecks_field_and_suppression():
    s, p, o, c = board(
        {
            "kind": "continuous",
            "trigger": "passive",
            "scope": "self",
            "attackRequires": {"prefix": "Team Rocket's ", "count": 4},
        }
    )
    c.name = "Team Rocket's Test"
    assert not allowed(c, c.attacks[0], s)
    crew = [registry.get("P01-005")() for _ in range(3)]
    for member in crew:
        member.name = "Team Rocket's Member"
    zone(p, "bench", crew)
    assert allowed(c, c.attacks[0], s)
    p.bench.pop()
    assert not allowed(c, c.attacks[0], s)
    o.active[0].ability = [SimpleNamespace(suppresses_opponent_active_abilities=True)]
    assert allowed(c, c.attacks[0], s)
