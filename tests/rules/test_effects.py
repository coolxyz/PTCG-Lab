"""Rule examples, independent of self-play completion."""
import pytest
from ptcg.core.card_registry import registry
from ptcg.core.action import (AttackAction, UseAbilityAction, UseItemAction, UseSupporterAction,
                             UseStadiumAction, RetreatAction, PlayPokemonAction, ChooseCardAction)
from ptcg.core.enums import CardType as T, CardPosition as Z, PokemonPosition as P, Stage
from ptcg.core.reducer import _handle_knockout
from ptcg.utils.utils import current_player, opponent_player, switch_pokemon
from packages.rules.adapter import Adapter
from packages.rules.engine import Gholdengo
from packages.rules.effects import (Iono, SuperiorEnergyRetrieval, Artazon, RescueBoard, Mew,
                                 TechnicalMachine, Drakloak, Munkidori, Dragapult, Fezandipiti)
from packages.rules.additions import (Tatsugiri, ExpShare, NeoUpperEnergy, LuminousEnergy,
                                   GimmighoulSV7a, DunsparceSV2P)
from packages.rules.core_fixes import check_energy, refresh_energy, discard_pokemon, end_turn_tools
from test_rules import finish_setup


def context():
    game=Adapter(0);finish_setup(game)
    s=game.env.gamestate;p=current_player(s);o=opponent_player(s)
    p.firstTurn=o.firstTurn=False
    p.supporterPlayedTurn=o.supporterPlayedTurn=False
    p.hand=[];p.bench=[];p.discard=[]
    return s,p,o


def zone(p,name,cards):
    setattr(p,name,cards)
    for i,c in enumerate(cards):
        c.index=i+1;c.cardPosition=Z[name.upper()]
        if name in ('active','bench'):c.position=P[name.upper()]
    return cards


def energy():return registry.get('SVE-008')()


def drive(gen, picker=None):
    seen=[]
    try:
        item=next(gen)
        while True:
            info=item[3];actions=info['raw_available_actions'];seen.append(info)
            choice=picker(actions,info,len(seen)) if picker else list(actions)[-1]
            item=gen.send(choice)
    except StopIteration:
        return seen


@pytest.mark.parametrize('cost,available,expected',[
    ([T.FIRE,T.PSYCHIC],[T.ANY,T.ANY],True),
    ([T.FIRE,T.PSYCHIC],[T.ANY,T.COLORLESS],False),
    ([T.FIRE,T.FIRE],[T.FIRE,T.ANY],True),
    ([T.FIRE,T.COLORLESS],[T.FIRE],False),
    ([T.COLORLESS,T.COLORLESS],[T.FIRE,T.PSYCHIC],True)])
def test_energy_no_double_spend_or_mutation(cost,available,expected):
    original=available[:];assert check_energy(cost,available)==expected;assert available==original


def test_conditional_special_energy():
    p=Dragapult();neo=NeoUpperEnergy();lum=LuminousEnergy();p.attachment=[neo,lum]
    refresh_energy(p);assert p.energy==[T.ANY,T.ANY,T.COLORLESS]
    p.stage=Stage.STAGE_1;refresh_energy(p);assert p.energy==[T.COLORLESS,T.COLORLESS]
    p.attachment.remove(neo);refresh_energy(p);assert p.energy==[T.ANY]
    p.attachment.append(LuminousEnergy());refresh_energy(p);assert p.energy==[T.COLORLESS]*2


def test_iono_keeps_deck_order_and_opponent_supporter_flag():
    s,p,o=context();iono=Iono();zone(p,'hand',[iono,energy()]);zone(o,'hand',[energy()])
    left=list(p.left);oleft=list(o.left)
    iono.reduce_action(UseSupporterAction(p.id,iono),s)
    assert p.hand==left[:6] and o.hand==oleft[:6]
    assert p.left[:len(left)-6]==left[6:]
    assert not o.supporterPlayedTurn and p.supporterPlayedTurn


def test_iono_no_return_no_draw():
    s,p,o=context();iono=Iono();zone(p,'hand',[iono]);zone(o,'hand',[])
    left=list(p.left);iono.reduce_action(UseSupporterAction(p.id,iono),s)
    assert p.hand==[] and p.left==left and o.hand==[]


def test_retrieval_excludes_cost_energy_and_requires_discard():
    s,p,o=context();item=SuperiorEnergyRetrieval();paid=[energy(),energy()]
    zone(p,'hand',[item]+paid);assert not item.get_actions(s)
    old=energy();zone(p,'discard',[old])
    def choose(actions,info,n):
        if n==2: assert actions.candidates==[old]
        return list(actions)[-1]
    drive(item.reduce_action(UseItemAction(p.id,item),s),choose)
    assert p.hand==[old] and all(c in p.discard for c in paid)


def test_artazon_exclusion_optional_and_same_name():
    s,p,o=context();stadium=Artazon();s.stadium=[stadium]
    basic=GimmighoulSV7a();zone(p,'left',[basic,Mew(),Fezandipiti()])
    assert stadium.eligible(p)==[basic]
    def choose(actions,info,n):
        assert actions.candidates==[basic]
        return actions.action_for_candidate_indices([])
    drive(stadium.reduce_action(UseStadiumAction(p.id,stadium),s),choose)
    assert p.bench==[] and p.stadiumUsedTurn
    another=Artazon();zone(p,'hand',[another]);assert not another.get_actions(s)


def test_rescue_board_free_retreat_before_action_generation():
    s,p,o=context();a=GimmighoulSV7a();b=DunsparceSV2P();zone(p,'active',[a]);zone(p,'bench',[b])
    a.attachment=[RescueBoard()];a.energy=[]
    assert not any(isinstance(x,RetreatAction) for x in p.get_actions(s))
    a.hp=30
    assert any(isinstance(x,RetreatAction) for x in p.get_actions(s))
    assert a.retreat==[]


def test_nested_knockout_preserves_all_physical_cards():
    s,p,o=context();base=registry.get('TWM-128')();middle=Drakloak();top=Dragapult();e=energy()
    top.evolved=[middle];middle.evolved=[base];top.attachment=[e];zone(p,'active',[top])
    discard_pokemon(p,top)
    assert set(map(id,p.discard))=={id(base),id(middle),id(top),id(e)}
    assert all(not c.evolved for c in p.discard if hasattr(c,'evolved'))


def test_tatsugiri_can_enter_play_and_only_active_ability():
    s,p,o=context();card=Tatsugiri();zone(p,'hand',[card])
    drive(card.reduce_action(PlayPokemonAction(p.id,card,P.BENCH),s))
    assert card in p.bench and card not in p.hand and not card.get_actions(s)
    switch_pokemon(p.active[0],card,p)
    assert any(isinstance(a,UseAbilityAction) for a in card.get_actions(s))


def test_mew_restart_per_instance():
    s,p,o=context();a=Mew();b=Mew();zone(p,'active',[a]);zone(p,'bench',[b])
    drive(a.reduce_action(UseAbilityAction(p.id,a,a.ability[0]),s));assert len(p.hand)==3
    zone(p,'hand',[])
    assert any(isinstance(x,UseAbilityAction) for x in b.get_actions(s))
    assert not any(isinstance(x,UseAbilityAction) for x in a.get_actions(s))


def test_mew_copies_variable_effect_without_mutating_defender_attack():
    s,p,o=context();mew=Mew();foe=Gholdengo();zone(p,'active',[mew]);zone(o,'active',[foe]);zone(p,'hand',[energy()])
    before=foe.attacks[0].damage
    drive(mew.reduce_action(AttackAction(p.id,mew,mew.attacks[0],foe),s))
    assert foe.hp==210 and foe.attacks[0].damage==before and len(p.discard)==1


def test_mew_mirror_no_recursion():
    s,p,o=context();mew=Mew();foe=Mew();zone(p,'active',[mew]);zone(o,'active',[foe])
    drive(mew.reduce_action(AttackAction(p.id,mew,mew.attacks[0],foe),s))
    assert s.turn==o.id and foe.hp==180


def test_drakloak_keeps_other_top_card_at_bottom():
    s,p,o=context();card=Drakloak();zone(p,'bench',[card]);left=list(p.left)
    def choose(actions,info,n):
        assert actions.candidates==left[:2]
        return actions.action_for_candidate_indices([0])
    drive(card.reduce_action(UseAbilityAction(p.id,card,card.ability[0]),s),choose)
    assert p.hand==left[:1] and p.left[-1] is left[1]
    assert not any(isinstance(x,UseAbilityAction) for x in card.get_actions(s))


def test_tool_attack_source_energy_and_end_turn_discard():
    s,p,o=context();holder=GimmighoulSV7a();tool=TechnicalMachine();zone(p,'active',[holder]);holder.attachment=[tool]
    assert not tool.get_actions(s)
    holder.energy=[T.METAL];action=tool.get_actions(s)[0]
    assert action.source is holder and action.effect_source is tool and len(action.attack.cost)==1
    end_turn_tools(s);assert tool in p.discard and not holder.attachment


@pytest.mark.parametrize("damage, moved", [(20, 20), (50, 30)])
def test_munkidori_moves_actual_counters(damage, moved):
    s,p,o=context();card=Munkidori();zone(p,'active',[card]);card.hp-=damage;card.energy=[T.ANY]
    before=o.active[0].hp
    def choose(actions,info,n):
        return list(actions)[-1] if n==2 else list(actions)[0]
    drive(card.reduce_action(UseAbilityAction(p.id,card,card.ability[0]),s),choose)
    assert card.hp==110-damage+moved and o.active[0].hp==before-moved and card.abilityUsed


def test_tera_blocks_damage_but_not_counters():
    s,p,o=context();card=Fezandipiti();target=Dragapult();zone(p,'active',[card]);zone(o,'bench',[target])
    def choose(actions,info,n):return actions.action_for_candidate_indices([1])
    drive(card.reduce_action(AttackAction(p.id,card,card.attacks[0],o.active[0]),s),choose)
    assert target.hp==320
    s.turn=p.id;attacker=Dragapult();zone(p,'active',[attacker]);o.active[0].hp=500
    drive(attacker.reduce_action(AttackAction(p.id,attacker,attacker.attacks[1],o.active[0]),s))
    assert target.hp==260


@pytest.mark.parametrize('attack_damage',[False,True])
def test_exp_share_only_attack_damage_defender_chooses(attack_damage):
    s,p,o=context();target=GimmighoulSV7a();holder=DunsparceSV2P();e=energy()
    zone(o,'active',[target]);zone(o,'bench',[holder]);target.attachment=[e];target.energy=list(e.provides);holder.attachment=[ExpShare()]
    def choose(actions,info,n):
        if attack_damage and n<=2: assert all(a.playerId==o.id for a in actions)
        return list(actions)[-1]
    drive(_handle_knockout(target,p,o,s,attack_damage=attack_damage),choose)
    assert (e in holder.attachment)==attack_damage
    assert (e in o.discard)!=attack_damage


def test_evolution_preserves_damage_and_clears_confusion():
    from ptcg.core.action import EvolvePokemonAction
    from ptcg.core.reducer import reduce_evolve_pokemon_action
    from ptcg.core.enums import SpecialCondition
    s,p,o=context();base=GimmighoulSV7a();evo=Gholdengo();zone(p,'active',[base]);zone(p,'hand',[evo])
    base.hp=40;base.special_condition=SpecialCondition.CONFUSED
    reduce_evolve_pokemon_action(EvolvePokemonAction(p.id,evo,base),s)
    assert evo.hp==230 and not hasattr(evo,'special_condition')
    assert evo.evolved==[base]


def test_prime_catcher_without_own_bench():
    from packages.rules.effects import PrimeCatcher
    s,p,o=context();card=PrimeCatcher();zone(p,'hand',[card]);target=GimmighoulSV7a();zone(o,'bench',[target])
    assert card.get_actions(s)
    drive(card.reduce_action(UseItemAction(p.id,card),s))
    assert o.active==[target] and card in p.discard


def test_mew_copies_dragapult_plain_attack():
    s,p,o=context();mew=Mew();foe=Dragapult();zone(p,'active',[mew]);zone(o,'active',[foe])
    drive(mew.reduce_action(AttackAction(p.id,mew,mew.attacks[0],foe),s),
          lambda actions,info,n:actions.action_for_candidate_indices([0]))
    assert foe.hp==250 and s.turn==o.id


def test_tm_can_evolve_new_bench_on_second_players_first_turn():
    s,p,o=context();holder=GimmighoulSV7a();tool=TechnicalMachine();base=GimmighoulSV7a();evo=Gholdengo()
    zone(p,'active',[holder]);zone(p,'bench',[base]);zone(p,'left',[evo,energy()]);holder.attachment=[tool];holder.energy=[T.METAL]
    p.firstTurn=True;s.starting_player=o.id
    drive(tool.reduce_action(tool.get_actions(s)[0],s))
    assert p.bench==[evo] and evo.evolved==[base] and tool in p.discard


def test_confusion_tails_prevents_attack():
    from ptcg.core.enums import SpecialCondition, Coin
    from unittest.mock import patch
    game=Adapter(0);finish_setup(game);s=game.env.gamestate;p=current_player(s);o=opponent_player(s)
    card=GimmighoulSV7a();zone(p,'active',[card]);card.special_condition=SpecialCondition.CONFUSED;before=o.active[0].hp
    action=AttackAction(p.id,card,card.attacks[1],o.active[0]);gen=game.env._reduce_action();next(gen)
    with patch('ptcg.utils.utils.flip_coin',return_value=Coin.TAIL), pytest.raises(StopIteration):gen.send(action)
    assert card.hp==40 and o.active[0].hp==before and s.turn==o.id
    bench=DunsparceSV2P();zone(p,'bench',[bench]);switch_pokemon(card,bench,p)
    assert not hasattr(card,'special_condition')


def test_ciphermaniac_with_single_card_deck():
    from packages.rules.effects import Ciphermaniac
    s,p,o=context();card=Ciphermaniac();last=energy();zone(p,'hand',[card]);zone(p,'left',[last])
    assert card.get_actions(s)
    drive(card.reduce_action(UseSupporterAction(p.id,card),s))
    assert p.left==[last] and card in p.discard and not s.pending_cards


def test_public_up_to_choices_cannot_select_zero():
    s,p,o=context();item=SuperiorEnergyRetrieval();zone(p,'hand',[item,energy(),energy()]);zone(p,'discard',[energy()])
    def choose(actions,info,n):
        if n==2:
            with pytest.raises(ValueError):actions.action_for_candidate_indices([])
        return list(actions)[-1]
    drive(item.reduce_action(UseItemAction(p.id,item),s),choose)
    s,p,o=context();card=Munkidori();zone(p,'active',[card]);card.hp-=20;card.energy=[T.ANY]
    def choose_counter(actions,info,n):
        if n==2:assert [a.value for a in actions]==[1,2]
        return list(actions)[-1]
    drive(card.reduce_action(UseAbilityAction(p.id,card,card.ability[0]),s),choose_counter)


def test_no_effect_trainers_and_draw_abilities_not_offered():
    s,p,o=context();zone(p,'left',[]);zone(p,'active',[Gholdengo()]);p.hand=[registry.get('PAF-087')()]
    assert not any(isinstance(a,(UseSupporterAction,UseAbilityAction)) for a in p.get_actions(s))
    iono=Iono();zone(p,'hand',[iono]);zone(o,'hand',[])
    assert not iono.get_actions(s)


@pytest.mark.parametrize("has_supporter", [False, True])
def test_tatsugiri_inspects_all_six_before_optional_supporter(has_supporter):
    s,p,o=context(); card=Tatsugiri(); zone(p,'active',[card])
    top=[energy() for _ in range(6)]
    supporter=Iono()
    if has_supporter: top[2]=supporter
    # A Supporter outside the six must never become selectable.
    outside=Iono()
    zone(p,'left',top+[outside]); before=set(map(id,p.left))
    gen=card.reduce_action(UseAbilityAction(p.id,card,card.ability[0]),s)
    item=next(gen); info=item[3]
    assert info['prompt'].candidates == top
    assert info['prompt'].tips == ('tatsugiri_inspect_found' if has_supporter else 'tatsugiri_inspect_empty')
    choices=list(info['raw_available_actions']); assert len(choices)==1
    assert card.abilityUsed and not p.hand
    if has_supporter:
        item=gen.send(choices[0]); info=item[3]
        assert info['prompt'].candidates == [supporter]
        with pytest.raises(StopIteration): gen.send(list(info['raw_available_actions'])[-1])
        assert p.hand == [supporter]
    else:
        with pytest.raises(StopIteration): gen.send(choices[0])
        assert not p.hand
    assert set(map(id,p.left+p.hand)) == before
    assert outside in p.left and outside not in p.hand
