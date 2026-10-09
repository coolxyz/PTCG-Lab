"""Independent rule assertions for the new source-compiled recovery family."""
import copy

import pytest
from packages.battle import runtime  # load the checked overlay first
from packages.sync.normalize import normalize
from packages.sync.adapt import compile_basic
from packages.rules.plain import PlainPokemon
from packages.rules.adapter import Adapter
from packages.battle.agent import predict
from ptcg.core.action import AttackAction
from ptcg.core.enums import CardType, SpecialCondition
from test_effects import context, zone, drive
from ptcg.core.card_registry import registry


def test_real_upstream_new_combination_compiles_and_resolves():
    from pathlib import Path
    import json
    raw = json.loads(Path(__file__).with_name("real-recovery.json").read_text(encoding="utf-8"))
    normalized = normalize(raw)
    for cid, heal, cure in (("11442", 30, True), ("21671", 0, True)):
        face = normalized["cards"][cid]["face"]
        spec = compile_basic(face)
        assert spec and spec["attacks"][0]["mechanic"] == {"kind": "sync_self_recovery", "amount": heal, "cure": cure}
        s, p, o = context()
        c = type("RealRecovery", (PlainPokemon,), {"spec": spec})()
        zone(p, "active", [c])
        c.hp = 20
        c.poisoned = True
        c.energy = [getattr(CardType, t) for t in spec["attacks"][0]["cost"]]
        target_hp = o.active[0].hp
        drive(c.reduce_action(AttackAction(p.id, c, c.attacks[0], o.active[0]), s))
        assert c.hp == 20 + heal and not getattr(c, "poisoned", False)
        assert o.active[0].hp == target_hp


def recovery_card():
    spec = {"effectKey": "P4P-SYNCRECOVERY", "name": "Recovery fixture", "hp": 100, "type": "PSYCHIC", "stage": "BASIC", "evolvesFrom": [], "retreat": 1, "weakness": [], "resistance": [], "attacks": [{"name": "Recovery", "damage": 0, "cost": ["PSYCHIC"], "mechanic": {"kind": "sync_self_recovery", "amount": 30, "cure": True}}]}
    return type("Recovery", (PlainPokemon,), {"spec": spec})()


@pytest.mark.parametrize("start,expected", [(40, 70), (90, 100), (100, 100)])
def test_recovery_caps_hp_and_cures_without_harming_target(start, expected):
    s, p, o = context()
    c = recovery_card()
    zone(p, "active", [c])
    c.hp = start
    c.poisoned = True
    c.burned = True
    c.poison_damage = 30
    c.special_condition = SpecialCondition.CONFUSED
    c.energy = [CardType.PSYCHIC]
    target_hp = o.active[0].hp
    actions = c.get_actions(s)
    assert any(isinstance(a, AttackAction) for a in actions)
    drive(c.reduce_action(next(a for a in actions if isinstance(a, AttackAction)), s))
    assert c.hp == expected and o.active[0].hp == target_hp
    assert not getattr(c, "poisoned", False) and not getattr(c, "burned", False)
    assert not hasattr(c, "special_condition") and not hasattr(c, "poison_damage")


def test_healing_prohibition_does_not_prevent_condition_recovery():
    s, p, o = context()
    c = recovery_card()
    holder = registry.get("P01-005")()
    holder.spec = {"abilities": [{"kind": "healing_block", "trigger": "passive"}]}
    zone(o, "bench", [holder])
    zone(p, "active", [c])
    c.hp = 40
    c.poisoned = True
    attack = AttackAction(p.id, c, c.attacks[0], o.active[0])
    drive(c.reduce_action(attack, s))
    assert c.hp == 40 and not getattr(c, "poisoned", False)


def test_recovery_dto_has_no_hidden_state_and_ai_uses_visible_benefit():
    c = recovery_card()
    dto = Adapter._card(c.attacks[0])
    assert dto["recovery"] == {"amount": 30, "cure": True}
    own = {"active": [{"name": "x", "hp": 30, "maximumHp": 100, "poisoned": True}], "bench": [], "hand": []}
    opponent = {"active": [{"hp": 200}], "bench": [], "hand": None}
    view = {"stateVersion": 1, "observation": {"self": own, "opponent": opponent}, "decision": {"id": "d", "kind": "action", "options": [{"id": "recover", "actionType": "AttackAction", "attack": dto}, {"id": "hit", "actionType": "AttackAction", "attack": {"name": "hit", "damage": 20}}]}}
    command, _ = predict(view)
    assert command["choice"]["optionId"] == "recover"
    own["active"][0].update(hp=100, poisoned=False)
    command, _ = predict(view)
    assert command["choice"]["optionId"] == "hit"
