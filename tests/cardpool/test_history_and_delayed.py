from ptcg.core.action import EvolvePokemonAction, AttackAction
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType
from ptcg.core.reducer import reduce_evolve_pokemon_action
from ptcg.utils.utils import switch_pokemon, next_turn
from packages.rules.history import value, attack_used
from packages.rules.damage_events import deal
from packages.rules.healing import value as healed
from packages.rules.end_phase import finish_pending
from packages.rules.knockouts import resolve_group
from tests.cardpool.test_checkup import board
from test_effects import zone, drive


def test_history_tracks_physical_switch_evolution_and_damage():
    s,p,o=board()
    c=p.bench[0]
    switch_pokemon(p.active[0],c,p)
    assert value({"term":"moved_this_turn"},c,p,s)==1
    assert value({"term":"moved_this_turn"},p.bench[0],p,s)==0
    evolved=registry.get("P01-005")()
    zone(p,"hand",[evolved])
    reduce_evolve_pokemon_action(EvolvePokemonAction(p.id,evolved,c),s)
    assert value({"term":"evolved_this_turn","fromName":c.name},evolved,p,s)==1
    deal(o.active[0],evolved,10,s)
    s.turn_number+=1
    assert value({"term":"damage_last_turn"},evolved,p,s)==10
    assert value({"term":"evolved_this_turn"},evolved,p,s)==0


def test_heal_preview_does_not_record_and_effect_does():
    s,p,o=board();c=p.active[0];c.hp-=20
    assert healed(c,10,s)>c.hp
    assert value({"term":"healed_this_turn"},c,p,s)==0
    c.hp=healed(c,10,s,record=True)
    assert value({"term":"healed_this_turn"},c,p,s)==1


def test_attack_history_does_not_transfer_to_another_copy():
    s,p,o=board();c=p.active[0]
    action=AttackAction(p.id,c,c.attacks[0],o.active[0])
    attack_used(action,s)
    s.turn_number+=2
    assert value({"term":"attack_last_turn","name":action.attack.name},c,p,s)==1
    assert value({"term":"attack_last_turn","name":action.attack.name},p.bench[0],p,s)==0


def test_knockout_history_preserves_type_before_discard_reset():
    s,p,o=board();c=o.bench[0];c.cardType=CardType.FIGHTING;c.hp=0
    drive(resolve_group(s,damage_targets=[c]))
    s.turn_number+=1
    assert value({"term":"knockout_last_turn","type":"FIGHTING"},o.active[0],o,s)==1
    assert value({"term":"knockout_last_turn","type":"WATER"},o.active[0],o,s)==0


def test_delayed_damage_precedes_checkup_and_switch_removes_effect():
    s,p,o=board();c=p.active[0];c.hp=60
    c.delayed_attacks=[{"turn":s.turn_number,"delayed":True,"counters":20}]
    c.poisoned=True
    next_turn(s)
    drive(finish_pending(s))
    assert c.hp==30
    c.delayed_attacks=[{"turn":s.turn_number,"delayed":True,"knockout":True}]
    switch_pokemon(c,p.bench[0],p)
    assert not hasattr(c,"delayed_attacks")
