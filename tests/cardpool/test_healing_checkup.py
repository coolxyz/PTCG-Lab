from ptcg.core.card_registry import registry
from packages.rules.healing import value, eligible
from packages.rules.maximum_hp import maximum
from packages.rules.checkup import finish, needed
from packages.rules.entry_effects import effect
from tests.cardpool.test_checkup import board
from test_effects import drive, zone


def test_healing_lock_blocks_all_healing_but_not_counter_transfer_or_hp_modifiers():
    s,p,o=board()
    c=p.active[0]
    holder=o.bench[0]
    holder.spec={"abilities":[{"kind":"healing_block","trigger":"passive"}]}
    c.hp-=30
    before=c.hp
    assert value(c,20,s)==before and not eligible(c,s)
    drive(effect(p.bench[0],{"kind":"move_counter","target":"self"},s))
    assert c.hp==before+10
    holder.ability_blocked_turn=s.turn_number
    assert value(c,20,s)==before+30 and eligible(c,s)


def test_active_opponent_healing_lock_does_not_block_owner_or_benched_pokemon():
    s,p,o=board()
    p.bench[0].spec={"abilities":[{"kind":"healing_block","trigger":"passive","opponentActive":True}]}
    for player in (p,o):
        for c in player.active+player.bench:c.hp-=20
    assert not eligible(o.active[0],s)
    assert eligible(o.bench[0],s) and eligible(p.active[0],s)


def test_next_player_can_choose_healing_before_or_after_the_entire_condition_block():
    def run(conditions_first):
        s,p,o=board()
        c=p.active[0]
        base=maximum(c)
        c.hp=base;c.poisoned=True;c.poison_damage=10
        p.bench[0].spec={"abilities":[{"kind":"checkup","trigger":"passive","name":"Blessed Salt","teamHeal":20}]}
        assert needed(s)
        s.pending_checkup=True
        decisions=[]
        def pick(actions,info,n):
            choices=list(actions)
            if hasattr(choices[0],"label"):
                assert all(a.playerId==o.id for a in choices)
                decisions.append(1)
                return next(a for a in choices if (a.label=="特殊状态检查")==conditions_first)
            return choices[-1]
        drive(finish(s),pick)
        assert decisions==[1] and s.turn==o.id
        return base-c.hp
    assert run(True)==0
    assert run(False)==10


def test_checkup_ability_counters_skip_froslass_and_suppressed_abilities():
    s,p,o=board()
    holder=p.bench[0]
    holder.name="Froslass"
    holder.spec={"abilities":[{"kind":"checkup","trigger":"passive","abilityCounters":10,"exceptName":"Froslass"}]}
    target=o.active[0]
    from types import SimpleNamespace
    target.ability=[SimpleNamespace(name="Present")]
    target.ability_blocked_turn=s.turn_number
    before=target.hp;own=holder.hp
    s.pending_checkup=True
    drive(finish(s))
    assert target.hp==before and holder.hp==own
