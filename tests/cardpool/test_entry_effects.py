from copy import deepcopy
from types import SimpleNamespace
import pytest
from ptcg.core.action import PlayPokemonAction, EvolvePokemonAction
from ptcg.core.card_registry import registry
from ptcg.core.enums import PokemonPosition
from packages.rules.abilities import available
from packages.rules.entry_effects import effect
from packages.rules.plain import SPECS, PlainPokemon
from tests.cardpool.test_shared_abilities import board
from test_effects import context, zone, drive


def entry_source(trigger, effect_):
    return board({"kind": "on_entry", "trigger": trigger, "effect": effect_})


def test_hand_to_bench_draw_fires_once_and_can_be_declined():
    for accept in (False, True):
        s, p, o, c = entry_source("bench", {"kind": "draw", "count": 3})
        zone(p, "active", [registry.get("P01-005")()])
        zone(p, "hand", [c])
        before = len(p.left)
        drive(
            c.reduce_action(PlayPokemonAction(p.id, c, PokemonPosition.BENCH), s),
            lambda actions, info, turn: actions[-1 if accept else 0],
        )
        assert len(p.left) == before - (3 if accept else 0)
        assert not available(c, s)
        assert len(p.hand) == (3 if accept else 0)


def test_evolution_entry_respects_suppression_and_preserves_turn():
    for suppressed in (False, True):
        s, p, o, c = entry_source("evolve", {"kind": "draw", "count": 3})
        base = registry.get("P01-005")()
        zone(p, "active", [base])
        zone(p, "hand", [c])
        if suppressed:
            o.active[0].ability = [
                SimpleNamespace(suppresses_opponent_active_abilities=True)
            ]
        drive(c.reduce_action(EvolvePokemonAction(p.id, c, base), s))
        assert len(p.hand) == (0 if suppressed else 3)
        assert s.turn == p.id and not available(c, s)


def test_evolution_event_does_not_fire_bench_trigger():
    s, p, o, c = entry_source("bench", {"kind": "draw", "count": 3})
    base = registry.get("P01-005")()
    zone(p, "active", [base])
    zone(p, "hand", [c])
    assert drive(c.reduce_action(EvolvePokemonAction(p.id, c, base), s)) == []
    assert p.hand == []


def test_entry_counters_ignore_attack_effect_protection():
    s, p, o, c = entry_source(
        "bench", {"kind": "counters", "zone": "bench", "count": 2, "amount": 10}
    )
    targets = [registry.get("P01-005")() for _ in range(2)]
    zone(o, "bench", targets)
    for target in targets:
        target.attack_protection = {"turn": s.turn_number, "effects": True}
    before = [c.hp for c in targets]
    drive(effect(c, c.spec["abilities"][0]["effect"], s))
    assert [c.hp for c in targets] == [hp - 10 for hp in before]


def test_attachment_search_distributes_physical_cards_and_preserves_manual_attach():
    s, p, o, c = entry_source(
        "evolve",
        {"kind": "attach", "origin": "left", "count": 3, "basic": True, "type": "FIRE"},
    )
    energy = [registry.get("SVE-002")() for _ in range(3)]
    zone(p, "left", list(energy))
    p.energyPlayedTurn = False
    drive(effect(c, c.spec["abilities"][0]["effect"], s))
    attached = [e for holder in p.active + p.bench for e in holder.attachment]
    assert all(e in attached for e in energy) and not p.left
    assert not p.energyPlayedTurn
    assert sum(len(holder.energy) for holder in p.active + p.bench) == 3


def test_top_attach_cannot_select_cards_below_inspected_window():
    s, p, o, c = entry_source(
        "evolve",
        {"kind": "attach", "origin": "top", "top": 3, "count": "any", "basic": True},
    )
    top = [registry.get("SVE-002")() for _ in range(3)]
    bottom = registry.get("SVE-002")()
    zone(p, "left", top + [bottom])
    drive(effect(c, c.spec["abilities"][0]["effect"], s))
    assert p.left == [bottom]
    assert not s.public_reveals


def test_attachment_to_distinct_bench_targets_never_stacks_on_one():
    s, p, o, c = entry_source(
        "evolve",
        {
            "kind": "attach",
            "origin": "left",
            "type": "FIRE",
            "basic": True,
            "count": 3,
            "target": "bench",
            "distinctTargets": True,
        },
    )
    targets = [registry.get("P01-005")() for _ in range(3)]
    zone(p, "bench", targets)
    zone(p, "left", [registry.get("SVE-002")() for _ in range(4)])
    drive(effect(c, c.spec["abilities"][0]["effect"], s))
    assert [len(t.attachment) for t in targets] == [1, 1, 1]
    assert len(p.left) == 1


def test_attach_to_one_bench_keeps_all_selected_cards_on_same_recipient():
    s, p, o, c = entry_source(
        "bench",
        {
            "kind": "attach",
            "origin": "discard",
            "basic": True,
            "count": 2,
            "target": "bench",
            "singleTarget": True,
        },
    )
    targets = [registry.get("P01-005")() for _ in range(2)]
    zone(p, "bench", targets)
    zone(p, "discard", [registry.get("SVE-002")() for _ in range(2)])
    drive(effect(c, c.spec["abilities"][0]["effect"], s))
    assert sorted(len(t.attachment) for t in targets) == [0, 2]


def test_heal_discards_only_from_pokemon_actually_healed():
    s, p, o, c = entry_source(
        "evolve", {"kind": "heal", "target": "evolved", "discardEnergy": True}
    )
    from ptcg.core.enums import Stage

    for target in p.active + p.bench:
        target.stage = Stage.STAGE_1
        target.attachment = [registry.get("SVE-008")()]
    c.hp -= 10
    healthy = p.bench[0]
    drive(effect(c, c.spec["abilities"][0]["effect"], s))
    assert not c.attachment and len(healthy.attachment) == 1


ENTRY_SPECS = [
    s for s in SPECS if any(a["kind"] == "on_entry" for a in s.get("abilities", []))
]


@pytest.mark.parametrize("spec", ENTRY_SPECS, ids=lambda s: s["effectKey"])
def test_every_compiled_entry_can_resolve_without_becoming_an_activated_ability(spec):
    s, p, o = context()
    c = type("EntryFixture", (PlainPokemon,), {"spec": deepcopy(spec)})()
    entry = next(a for a in spec["abilities"] if a["kind"] == "on_entry")
    base = registry.get("P01-005")()
    zone(p, "active", [base])
    zone(p, "bench", [registry.get("P01-006")()])
    if entry.get("requiresTrait"):
        from ptcg.core.enums import PokemonRule
        p.bench[0].pokemonRule = PokemonRule[entry["requiresTrait"]]
    zone(p, "left", [registry.get("SVE-008")() for _ in range(8)])
    zone(p, "hand", [c])
    action = (
        PlayPokemonAction(p.id, c, PokemonPosition.BENCH)
        if entry["trigger"] == "bench"
        else EvolvePokemonAction(p.id, c, base)
    )
    drive(c.reduce_action(action, s))
    assert not available(c, s)
