"""Directed examples for the last GHIJ mechanisms, using real compiled faces."""
import pytest
from types import SimpleNamespace
from packages.sync.common import read
from packages.rules import expanded_operations as ops, expanded_entry as entries, expanded_trainers as trainers
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardPosition, PokemonPosition, CardType, Stage, SpecialCondition, Coin
from ptcg.core.action import AttackAction, UseItemAction, RetreatAction
from test_effects import context,zone,drive,energy

ROWS={str(r['sourceCardId']):r for r in read('artifacts/sync/reuse-support-summary.json')['remaining']}
def card(cid):
    rh=ROWS[str(cid)]['ruleHash']
    specs=read('data/cardpool/plain-pokemon.json')
    spec=next(c for k in ('cards','trainers','specialEnergies') for c in specs[k] if c.get('sourceRuleHash')==rh)
    return registry.get(spec['effectKey'])()
def board(cid,index=0):
    s,p,o=context();c=card(cid);zone(p,'active',[c]);zone(o,'bench',[])
    return s,p,o,c,o.active[0],AttackAction(p.id,c,c.attacks[index],o.active[0])

def test_bottom_draw_and_matching_hand_are_physical_cards():
    s,p,o,c,t,a=board(20827);old=list(p.left)
    drive(ops.resolve({'operation':'bottom_draw','count':3},a,s))
    assert p.hand==list(reversed(old[-3:])) and p.left==old[:-3]
    zone(o,'hand',[energy() for _ in range(4)])
    drive(ops.resolve({'operation':'match_hand_draw'},a,s))
    assert len(p.hand)==4 and len(p.left)==len(old)-4

def test_opponent_chooses_hand_shuffle_and_supporter_bottom():
    s,p,o,c,t,a=board(21124);zone(o,'hand',[energy() for _ in range(5)])
    seen=drive(ops.resolve({'operation':'opponent_shuffle_hand','count':3},a,s))
    assert len(o.hand)==2
    assert all(next(iter(i['raw_available_actions'])).playerId==o.id for i in seen)
    from packages.rules.effects import Iono
    supporter=Iono();zone(o,'hand',[energy(),supporter])
    drive(ops.resolve({'operation':'supporter_bottom'},a,s))
    assert o.left[-1] is supporter and len(o.hand)==1

def test_distinct_counters_hits_two_distinct_targets():
    s,p,o,c,t,a=board(21248);b=registry.get('P01-005')();zone(o,'bench',[b])
    before=(t.hp,b.hp)
    drive(ops.resolve({'operation':'distinct_counters','count':2,'amount':30},a,s))
    assert (t.hp,b.hp)==(before[0]-30,before[1]-30)

def test_damaged_spread_excludes_healthy_and_self():
    s,p,o,c,t,a=board(21876);b=registry.get('P01-005')();h=registry.get('P01-006')()
    zone(o,'bench',[b,h]);b.hp-=10;c.hp-=10
    before=(c.hp,b.hp,h.hp,t.hp)
    drive(ops.resolve({'operation':'damaged_spread','amount':50},a,s))
    assert (c.hp,b.hp,h.hp,t.hp)==(before[0],before[1]-50,before[2],before[3])

def test_distinct_types_search_never_selects_duplicate_type():
    s,p,o,c,t,a=board(11496)
    cards=[card(11496),card(11384),card(13916),card(21122)]
    zone(p,'left',cards)
    drive(ops.resolve({'operation':'distinct_types_search','count':3},a,s))
    assert len(p.hand)==3 and len({c.cardType for c in p.hand})==3

def test_mandatory_evolution_entry_mills_without_confirmation():
    s,p,o,c,t,a=board(11493);old=list(p.left)
    from packages.rules.entry_effects import resolve
    assert drive(resolve(c,'evolve',s))==[]
    assert p.discard==old[:5] and p.left==old[5:]

def test_indeedee_heals_and_cures_only_one_condition():
    s,p,o,c,t,a=board(18040);c.hp-=40;c.poisoned=True;c.burned=True
    drive(entries.resolve(c,{'op':'heal_cure_one','amount':30},s))
    assert c.hp==type(c)().hp-10
    assert sum(bool(getattr(c,x,False)) for x in ('poisoned','burned'))==1

def test_ditto_replaces_full_stack_and_does_not_trigger_bench_entry():
    s,p,o,c,t,a=board(11495);e=energy();c.attachment=[e];new=card(18040);zone(p,'left',[new]);c.hp-=10
    drive(entries.resolve(c,{'op':'transform_start'},s))
    assert p.active==[new] and c in p.discard and e in p.discard
    assert new.position==PokemonPosition.ACTIVE and new.firstTurnPlayed

@pytest.mark.parametrize('cid',[13066,13067,13068,18754,18755])
def test_fossil_item_enters_as_basic_and_can_be_discarded(cid):
    s,p,o=context();f=card(cid);zone(p,'hand',[f])
    assert f.superType.name=='TRAINER'
    drive(f.reduce_action(UseItemAction(p.id,f),s))
    assert f in p.bench and f.hp==60 and f.prize==1 and f.stage==Stage.BASIC
    f.poisoned=True;f.special_condition=SpecialCondition.PARALYZED
    from packages.rules.maximum_hp import reconcile
    reconcile(s)
    assert not getattr(f,'poisoned',False) and getattr(f,'special_condition',SpecialCondition.NONE)==SpecialCondition.NONE
    drive(f.reduce_action(f.get_actions(s)[0],s))
    assert f in p.discard and f.superType.name=='TRAINER'

def fossil_in_play(cid,s,p):
    f=card(cid);zone(p,'hand',[f]);drive(f.reduce_action(UseItemAction(p.id,f),s));return f

def test_amber_blocks_opponent_ability_counters_not_own_or_attack():
    s,p,o=context();f=fossil_in_play(13068,s,p)
    from packages.rules.ability_protection import blocked
    assert blocked(f,o.active[0],s) and not blocked(f,p.active[0],s)
    s.turn=o.id
    from packages.rules.entry_effects import effect
    drive(effect(o.active[0],{'kind':'counters','zone':'bench','count':1,'amount':20},s))
    assert f.hp==60
    from packages.rules.protection import blocked as attack_blocked
    assert not attack_blocked(f,s,'effects',o.active[0])

def test_cover_protects_attack_effects_but_takes_damage_and_dome_reduces30():
    s,p,o=context();cover=fossil_in_play(18755,s,p)
    from packages.rules.protection import blocked
    from packages.rules.core_fixes import shield_damage
    assert blocked(cover,s,'effects',o.active[0])
    assert shield_damage(cover,50,s,o.active[0])==50
    dome=fossil_in_play(13067,s,p)
    assert shield_damage(dome,50,s,o.active[0])==20

def test_special_energy_hp_bonus_and_retaliation():
    s,p,o,c,t,a=board(11403);from packages.rules.maximum_hp import reconcile,maximum
    spike=card(20172);c.attachment=[spike];c.hp-=30;reconcile(s)
    assert maximum(c)==350 and c.hp==320
    from packages.rules.damage_events import deal,finish
    before=t.hp;deal(t,c,10,s);finish(s)
    assert t.hp==before-20
    c.attachment=[];reconcile(s)
    assert maximum(c)==250 and c.hp==210

@pytest.mark.parametrize('damage,expected',[(60,0),(70,70)])
def test_metapod_final_damage_threshold(damage,expected):
    s,p,o,c,t,a=board(21099);c.damage_shield={'turn':s.turn_number,'amount':0,'maximum':60}
    from packages.rules.core_fixes import shield_damage
    assert shield_damage(c,damage,s,t)==expected

@pytest.mark.parametrize('heads,paralyzed',[(0,False),(1,False),(2,True),(4,True)])
def test_butterfree_coin_count_shares_damage_and_status_rolls(monkeypatch,heads,paralyzed):
    s,p,o,c,t,a=board(21100);t.hp=500;t.weakness=[];t.resistance=[]
    rolls=iter([Coin.HEAD]*heads+[Coin.TAIL]*(4-heads))
    monkeypatch.setattr('packages.rules.coin_branches.flip_coin',lambda state:next(rolls))
    drive(c.reduce_action(a,s))
    assert t.hp==500-heads*60
    assert (getattr(t,'special_condition',SpecialCondition.NONE)==SpecialCondition.PARALYZED)==paralyzed

def test_bench_attack_actions_and_resolution_use_bench_source():
    s,p,o,c,t,a=board(11428,1);active=card(11496);zone(p,'active',[active]);zone(p,'bench',[c]);c.energy=[CardType.PSYCHIC]*2
    actions=c.get_actions(s)
    assert len([x for x in actions if isinstance(x,AttackAction)])==1
    t.hp=300;t.weakness=[];t.resistance=[]
    drive(c.reduce_action(a,s));assert t.hp==180

@pytest.mark.parametrize('cid,op',[('14363','parasol_redraw'),('19519','discard_search_three'),('20773','heal_cure')])
def test_supporter_compositions(cid,op):
    s,p,o=context();c=card(cid);zone(p,'hand',[energy(),energy()])
    if op=='parasol_redraw':
        p.firstTurn=True;s.starting_player=o.id
        drive(trainers.resolve(c,{'op':op},s));assert len(p.hand)==8
    elif op=='discard_search_three':
        from packages.rules.effects import Iono
        choices=[card(11496),Iono(),energy()];zone(p,'left',list(choices))
        drive(trainers.resolve(c,{'op':op},s));assert p.hand==choices and len(p.discard)==2
    else:
        target=p.active[0];target.hp-=80;before=target.hp;target.poisoned=True;target.burned=True;target.special_condition=SpecialCondition.CONFUSED
        drive(trainers.resolve(c,{'op':op},s));assert target.hp==before+60
        assert not getattr(target,'poisoned',False) and not getattr(target,'burned',False) and getattr(target,'special_condition',SpecialCondition.NONE)==SpecialCondition.NONE

def test_drone_requires_both_heads(monkeypatch):
    for coins,expected in [([Coin.HEAD,Coin.TAIL],0),([Coin.HEAD,Coin.HEAD],1)]:
        s,p,o=context();c=card(14355);rolls=iter(coins)
        monkeypatch.setattr(trainers,'flip_coin',lambda state:next(rolls))
        drive(trainers.resolve(c,{'op':'double_coin_search'},s));assert len(p.hand)==expected

def test_daisy_prizes_remain_in_place():
    s,p,o=context();prizes=list(p.prize);deck=list(p.left)
    drive(trainers.resolve(card(14362),{'op':'draw_inspect_prizes'},s))
    assert p.prize==prizes and p.hand==deck[:2]

def evolution_for(base):
    specs=read('data/cardpool/plain-pokemon.json')['cards']
    return registry.get(next(c['effectKey'] for c in specs if c.get('evolvesFrom',[])[:1]==[base.name]))()

@pytest.mark.parametrize('second,allowed',[(True,True),(False,False)])
def test_spearow_second_players_first_turn_evolution(second,allowed):
    s,p,o,c,t,a=board(11384);e=evolution_for(c);zone(p,'hand',[e]);c.firstTurnPlayed=True;p.firstTurn=True
    s.starting_player=o.id if second else p.id
    from ptcg.core.action import EvolvePokemonAction
    assert any(isinstance(x,EvolvePokemonAction) and x.target is c for x in p.get_actions(s))==allowed

@pytest.mark.parametrize('cid',[13066,13067,13068,18754,18755])
def test_fossils_can_evolve_and_retain_physical_stack(cid):
    s,p,o=context();f=fossil_in_play(cid,s,p);e=evolution_for(f);zone(p,'hand',[e]);f.firstTurnPlayed=False
    from ptcg.core.action import EvolvePokemonAction
    from ptcg.core.reducer import reduce_evolve_pokemon_action
    reduce_evolve_pokemon_action(EvolvePokemonAction(p.id,e,f),s)
    assert e in p.bench and f in e.evolved

def test_grand_tree_two_stage_evolution_from_deck():
    s,p,o,c,t,a=board(11384);c.firstTurnPlayed=False
    # Use an actual three-stage chain.
    from packages.rules.plain import SPECS
    base=next(registry.get(x['effectKey'])() for x in SPECS if x['name']=='Caterpie' and x['stage']=='BASIC')
    first=card(21099);second=card(21100);zone(p,'active',[base]);base.firstTurnPlayed=False
    zone(p,'left',[first,second]);drive(trainers.great_tree(card(18775),s))
    assert p.active==[second] and first in second.evolved
    assert base in first.evolved

def test_grand_tree_respects_first_turn_and_evolution_lock():
    s,p,o,c,t,a=board(11384);c.firstTurnPlayed=False;e=evolution_for(c);zone(p,'left',[e]);p.firstTurn=True
    assert drive(trainers.great_tree(card(18775),s))==[] and p.active==[c]
    p.firstTurn=False;c.evolution_blocked_turn=s.turn_number
    assert drive(trainers.great_tree(card(18775),s))==[] and p.active==[c]

def test_sandshrew_blocks_only_opponent_trainer_recycling_to_deck():
    s,p,o,c,t,a=board(11390)
    from packages.rules.effects import Iono
    from ptcg.utils.utils import move_cards
    trainer=Iono();e=energy();zone(o,'discard',[trainer,e]);s.effect_context={'kind':'item','owner':o.id,'fromHand':True}
    move_cards([trainer,e],(o.id,CardPosition.DISCARD),(o.id,CardPosition.LEFT),s)
    assert trainer in o.discard and e in o.left
    move_cards(trainer,(o.id,CardPosition.DISCARD),(o.id,CardPosition.HAND),s)
    assert trainer in o.hand

def test_omastar_retreat_lock_kabutops_multiplier_and_varoom_resistance():
    s,p,o,c,t,a=board(11502);t.energy=[CardType.COLORLESS]*6
    s.turn=o.id
    assert not any(isinstance(x,RetreatAction) for x in o.get_actions(s))
    kab=card(11504);zone(p,'bench',[kab]);t.weakness=[c.cardType]
    from packages.rules.pokemon_types import weakness_resistance
    assert weakness_resistance(c,t,50,s)==200
    varoom=card(13916);c.cardType=CardType.GRASS
    assert weakness_resistance(c,varoom,50,s)==30

def test_porygon_changes_weakness_until_switch():
    s,p,o,c,t,a=board(11500);b=card(11496);zone(o,'bench',[b])
    drive(ops.resolve({'operation':'choose_weakness'},a,s));assert t.weakness_override['type']=='DRAGON'
    from ptcg.utils.utils import switch_pokemon
    switch_pokemon(t,b,o);assert not hasattr(t,'weakness_override')

def test_paradise_affects_only_psyduck_and_root_adds_cost():
    s,p,o=context();resort=card(10888);s.stadium=[resort]
    from packages.rules.stadiums import modifiers
    c=p.active[0];old=c.name;c.name='Psyduck';assert modifiers(c,s)[0]['retreatLess']==1
    c.name=old;assert not modifiers(c,s)
    f=fossil_in_play(18754,s,p);zone(p,'active',[f]);zone(p,'bench',[])
    from packages.rules.modifiers import rules
    t=o.active[0];t.stage=Stage.BASIC;assert any(r.get('attackMore')==1 for r in rules(t,s))
    t.stage=Stage.STAGE_1;assert not any(r.get('attackMore') for r in rules(t,s))

def test_helix_blocks_only_opponent_stadium_from_hand():
    s,p,o=context();f=fossil_in_play(13066,s,p);zone(p,'active',[f]);zone(p,'bench',[])
    from packages.rules.hand_locks import allowed
    from ptcg.core.action import PutStadiumAction
    stadium=card(10888);zone(o,'hand',[stadium])
    assert not allowed(PutStadiumAction(o.id,stadium),o,s)
    assert allowed(PutStadiumAction(p.id,stadium),p,s)

@pytest.mark.parametrize('cid',[18747,18752])
def test_first_turn_items_unavailable_after_first_turn(cid):
    s,p,o=context();c=card(cid);zone(p,'hand',[c]);o.active[0].attachment=[energy()]
    assert c.get_actions(s)==[]
    p.firstTurn=True;s.starting_player=o.id;assert c.get_actions(s)
    s.starting_player=p.id;assert c.get_actions(s)==[]

def test_bell_searches_only_supporter_and_tail_returns_energy():
    s,p,o=context();from packages.rules.effects import Iono
    supporter=Iono();zone(p,'left',[energy(),supporter])
    drive(trainers.resolve(card(18747),{'op':'supporter_bell'},s));assert p.hand==[supporter]
    e=energy();o.active[0].attachment=[e];e.index=1
    drive(trainers.resolve(card(18752),{'op':'energy_hand'},s));assert e in o.hand and not o.active[0].attachment

def test_scramble_switch_transfers_energy_not_tools():
    s,p,o=context();old=p.active[0];new=card(11496);zone(p,'bench',[new]);e=energy();e.index=1;old.attachment=[e]
    drive(trainers.resolve(card(18750),{'op':'switch_transfer'},s))
    assert p.active==[new] and e in new.attachment and not old.attachment

@pytest.mark.parametrize('bottom',[True,False])
def test_deduction_kit_preserves_rest_of_deck(bottom):
    s,p,o=context();deck=list(p.left)
    def picker(actions,info,n):
        options=list(actions)
        if hasattr(options[0],'value'):return options[0 if bottom else -1]
        return options[-1]
    drive(trainers.resolve(card(18749),{'op':'order_or_bottom'},s),picker)
    assert (p.left[:-3]==deck[3:] and set(p.left[-3:])==set(deck[:3])) if bottom else (p.left[3:]==deck[3:] and set(p.left[:3])==set(deck[:3]))

def test_black_belt_bonus_only_opponent_active_ex():
    s,p,o=context();from ptcg.core.enums import PokemonType
    drive(trainers.resolve(card(18060),{'op':'ex_team_bonus'},s))
    from packages.rules.modifiers import attack_damage
    target=o.active[0];target.pokemonType=PokemonType.EX
    assert attack_damage(p.active[0],target,50,s)==90
    target.pokemonType=PokemonType.NORMAL;assert attack_damage(p.active[0],target,50,s)==50

def test_entry_deploy_tool_and_hidden_shuffle(monkeypatch):
    s,p,o=context();from packages.rules.effects import RescueBoard
    source=card(21137);zone(p,'bench',[source]);tool=RescueBoard();zone(p,'left',[tool])
    drive(entries.resolve(source,{'op':'attach_tool'},s));assert tool in source.attachment
    basic=card(11496);zone(o,'hand',[basic]);zone(o,'bench',[])
    drive(entries.resolve(source,{'op':'deploy_opponent'},s));assert basic in o.bench and basic.firstTurnPlayed
    hand=[energy(),energy()];zone(o,'hand',list(hand));monkeypatch.setattr(entries,'flip_coin',lambda state:Coin.HEAD)
    drive(entries.resolve(source,{'op':'coin_shuffle_hand','flips':2},s));assert not o.hand and set(hand)<=set(o.left)

def test_beedrill_and_abomasnow_conditions():
    from packages.rules.attack_math import damage
    s,p,o,c,t,a=board(11378);m=c.spec['attacks'][0]['mechanic']['steps'][0]
    assert damage(m,a,s)==150
    zone(p,'hand',[energy()]);assert damage(m,a,s)==30
    s,p,o,c,t,a=board(21116,1);m=c.spec['attacks'][1]['mechanic']
    c.energy=[CardType.GRASS];assert damage(m,a,s)==120
    c.energy=[CardType.GRASS]*2;assert damage(m,a,s)==240

def test_tauros_returns_energy_only_from_stage_two():
    s,p,o,c,t,a=board(18025);energies=[energy(),energy()]
    t.attachment=list(energies)
    for i,e in enumerate(energies):e.index=i+1
    t.stage=Stage.STAGE_1;drive(ops.resolve({'operation':'stage_energy_return'},a,s));assert t.attachment==energies
    t.stage=Stage.STAGE_2;drive(ops.resolve({'operation':'stage_energy_return'},a,s));assert not t.attachment and set(energies)<=set(o.hand)

def test_butterfree_shuffle_fails_without_bench_then_returns_whole_stacks():
    s,p,o,c,t,a=board(11375,1);drive(ops.resolve({'operation':'shuffle_pair'},a,s));assert p.active==[c]
    b=card(11496);zone(o,'bench',[b]);e=energy();c.attachment=[e]
    drive(ops.resolve({'operation':'shuffle_pair'},a,s));assert not p.active and c in p.left and e in p.left and b in o.left

def test_aerodactyl_devolves_only_active():
    s,p,o,c,t,a=board(11505,1)
    from ptcg.core.reducer import reduce_evolve_pokemon_action
    from ptcg.core.action import EvolvePokemonAction
    base=card(11384);zone(o,'active',[base]);e=evolution_for(base);zone(o,'hand',[e]);s.turn=o.id
    reduce_evolve_pokemon_action(EvolvePokemonAction(o.id,e,base),s);s.turn=p.id;a.target=e
    from packages.rules.remaining_attacks import resolve
    drive(resolve({'operation':'devolve','activeOnly':True,'destination':'hand'},a,s))
    assert o.active==[base] and e in o.hand

def test_vivillon_status_choice_only_on_heads(monkeypatch):
    for coin,expected in [(Coin.TAIL,False),(Coin.HEAD,True)]:
        s,p,o,c,t,a=board(12667);t.hp=500
        monkeypatch.setattr('packages.rules.coin_branches.flip_coin',lambda state:coin)
        drive(c.reduce_action(a,s))
        assert (getattr(t,'special_condition',SpecialCondition.NONE)==SpecialCondition.CONFUSED)==expected
