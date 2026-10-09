"""New effect implementations with internal engine IDs, not printing numbers.

Evidence links and correspondence records live in data/engine/effect-sources.json.
"""
from ptcg.core.card import PokemonCard, ToolCard, EnergyCard
from ptcg.core.enums import *
from ptcg.core.action import AttackAction, PlayPokemonAction, UseAbilityAction, UseToolAction, AttachEnergyAction, choose_card_actions
from ptcg.core.attack import Attack
from ptcg.core.ability import ActiveAbility
from ptcg.core.reducer import reduce_attack_action, reduce_play_pokemon_action, reduce_choose_card_actions, reduce_attach_energy_action
from ptcg.utils.utils import current_player, current_all_pokemon, opponent_active, check_energy, move_cards, shuffle_cards, can_attach_tool, can_attach_energy


class Tatsugiri(PokemonCard):
    def __init__(self):
        super().__init__()
        self.id, self.set_name, self.number, self.name = 'P01-001', 'P01', '001', 'Tatsugiri'
        self.hp, self.stage, self.cardType = 70, Stage.BASIC, CardType.DRAGON
        self.pokemonType, self.pokemonRule, self.prize = PokemonType.NORMAL, PokemonRule.NONE, 1
        self.retreat, self.weakness, self.resistance = [CardType.COLORLESS], [], []
        self.energy, self.attachment, self.evolved = [], [], []
        self.abilityUsed = False
        self.ability=[ActiveAbility({'name':'Attract Customers','abilityType':AbilityType.ACTIVE_ABILITY,'onceUsedPerTurn':True,
            'text':'While Active, once per turn inspect top six; reveal a Supporter to hand, then shuffle.'})]
        self.attacks=[Attack({'name':'Surf','damage':50,'cost':[CardType.FIRE,CardType.WATER],'text':''})]

    def get_actions(self,state):
        if self.position != PokemonPosition.ACTIVE: return []
        result=[]
        if not self.abilityUsed and current_player(state).left:
            result.append(UseAbilityAction(state.turn,self,self.ability[0]))
        if opponent_active(state) and check_energy(self.attacks[0].cost,self.energy):
            result.append(AttackAction(state.turn,self,self.attacks[0],opponent_active(state)[0]))
        return result

    def reduce_action(self,action,state):
        if isinstance(action,PlayPokemonAction):
            reduce_play_pokemon_action(action,state)
        elif isinstance(action,UseAbilityAction):
            p=current_player(state)
            self.abilityUsed=True
            top=p.left[:6]
            candidates=[c for c in top if getattr(c,'trainerType',None)==TrainerType.SUPPORTER]
            yield from reduce_choose_card_actions(choose_card_actions(
                p.id,p.id,0,0,top,source=self,
                tips='tatsugiri_inspect_found' if candidates else 'tatsugiri_inspect_empty'),state)
            # Search a hidden zone permits failing to find a qualifying card.
            if candidates:
                selected=yield from reduce_choose_card_actions(choose_card_actions(p.id,p.id,0,1,candidates,source=self),state)
                move_cards(selected,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),state)
                state.public_reveals.append({'kind':'search_reveal','actor':p.id.name,'cards':[c.to_dict() for c in selected]})
            shuffle_cards(p.left,state)
        elif isinstance(action,AttackAction):
            yield from reduce_attack_action(action,state)


class ExpShare(ToolCard):
    def __init__(self):
        super().__init__()
        self.id,self.set_name,self.number,self.name='P01-002','P01','002','Exp. Share'
        self.cardType=CardType.NONE
        self.text='On own Active being knocked out by opposing attack damage, optionally move one Basic Energy to this holder.'
        self.hasAttached=False
        self.attachedTo=None

    def get_actions(self,state):
        if self in current_player(state).hand:
            return [UseToolAction(state.turn,self,c) for c in current_all_pokemon(state) if can_attach_tool(c)]
        return []

    def reduce_action(self,action,state):
        if isinstance(action,UseToolAction):
            p=current_player(state);target=action.target
            zone=CardPosition.ACTIVE_ATTACHMENT if target in p.active else CardPosition.BENCH_ATTACHMENT
            move_cards(self,(p.id,CardPosition.HAND),(p.id,zone,target.index),state)
            self.hasAttached=True
            self.attachedTo=[target]


class NeoUpperEnergy(EnergyCard):
    def __init__(self):
        super().__init__()
        self.id,self.set_name,self.number,self.name='P01-003','P01','003','Neo Upper Energy'
        self.energyType,self.cardType=EnergyType.SPECIAL,CardType.COLORLESS
        self.provides=[CardType.COLORLESS]
        self.text='One Colorless; on a Stage 2 Pokemon, two Energy of any types. ACE SPEC.'
        self.aceSpec=True

    def effective_provides(self,target):
        return [CardType.ANY]*2 if target.stage==Stage.STAGE_2 else [CardType.COLORLESS]

    def get_actions(self,state):
        return [AttachEnergyAction(state.turn,self,c) for c in current_all_pokemon(state)] if can_attach_energy(state) else []

    def reduce_action(self,action,state):
        if isinstance(action,AttachEnergyAction):
            reduce_attach_energy_action(action,state)
            from packages.rules.core_fixes import refresh_energy
            refresh_energy(action.target)


class LuminousEnergy(NeoUpperEnergy):
    def __init__(self):
        super().__init__()
        self.id,self.number,self.name='P01-004','004','Luminous Energy'
        self.aceSpec=False
        self.provides=[CardType.ANY]
        self.text='One Energy of any type; one Colorless if another Special Energy is attached.'

    def effective_provides(self,target):
        another=any(c is not self and isinstance(c,EnergyCard) and c.energyType==EnergyType.SPECIAL for c in target.attachment)
        return [CardType.COLORLESS] if another else [CardType.ANY]


from ptcg.cards.PAR.gimmighoul import PAR088Gimmighoul
from ptcg.utils.utils import next_turn


class GimmighoulSV7a(PAR088Gimmighoul):
    def __init__(self):
        super().__init__()
        self.id,self.set_name,self.number='P01-005','P01','005'
        self.attacks=[Attack({'name':'Minor Errand-Running','cost':[CardType.COLORLESS],'damage':0,'text':'Search up to two Basic Energy, reveal to hand, shuffle.'}),
                      Attack({'name':'Tackle','cost':[CardType.COLORLESS]*3,'damage':50,'text':''})]

    def reduce_action(self,action,state):
        if isinstance(action,AttackAction) and action.attack.name==self.attacks[0].name:
            p=current_player(state)
            if self.id=='P01-005':
                cards=[c for c in p.left if isinstance(c,EnergyCard) and c.energyType==EnergyType.BASIC];limit=2
            else:
                cards=[c for c in p.left if isinstance(c,PokemonCard)];limit=1
            if cards:
                chosen=yield from reduce_choose_card_actions(choose_card_actions(p.id,p.id,0,min(limit,len(cards)),cards,source=self),state)
                move_cards(chosen,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),state)
                state.public_reveals.append({'kind':'search_reveal','actor':p.id.name,'cards':[c.to_dict() for c in chosen]})
            shuffle_cards(p.left,state)
            next_turn(state)
        elif isinstance(action,AttackAction):
            yield from reduce_attack_action(action,state)
        else:
            yield from super().reduce_action(action,state)


class DunsparceSV2P(GimmighoulSV7a):
    def __init__(self):
        super().__init__()
        self.id,self.number,self.name='P01-006','006','Dunsparce'
        self.cardType=CardType.COLORLESS
        self.retreat=[CardType.COLORLESS]
        self.weakness=[CardType.FIGHTING];self.resistance=[]
        self.attacks=[Attack({'name':'Find a Friend','cost':[CardType.COLORLESS],'damage':0,'text':'Search a Pokemon, reveal to hand, shuffle.'}),
                      Attack({'name':'Bite','cost':[CardType.COLORLESS]*3,'damage':50,'text':''})]
