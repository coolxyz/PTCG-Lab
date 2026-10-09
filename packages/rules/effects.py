"""Corrections to effects reached by the P0.1 target deck candidates."""
from packages.rules.maximum_hp import maximum

from ptcg.cards.TEF.ciphermaniacs_codebreaking import TEF145CiphermaniacsCodebreaking
from ptcg.cards.TEF.dudunsparce import TEF129Dudunsparce
from ptcg.cards.PAR.professor_turos_scenario import PAR257ProfessorTurosScenario
from ptcg.core.card import PokemonCard
from ptcg.core.action import UseSupporterAction, UseAbilityAction, AttackAction, choose_card_actions
from ptcg.core.enums import CardPosition, PokemonPosition
from ptcg.core.reducer import reduce_choose_card_actions, _force_active_replacement
from ptcg.utils.utils import current_player, move_cards, shuffle_cards, check_energy, opponent_active


class Turo(PAR257ProfessorTurosScenario):
    def get_actions(self, state):
        p = current_player(state)
        from packages.rules.zone_guards import hand_return_forbidden
        if hand_return_forbidden(p, state):
            return []
        return [UseSupporterAction(p.id, self)] if not p.supporterPlayedTurn and p.active + p.bench else []

    def reduce_action(self, action, state):
        p = current_player(state)
        if not getattr(self, "copied_effect", False):
            move_cards(self, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state)
            p.supporterPlayedTurn = True
        chosen = yield from reduce_choose_card_actions(
            choose_card_actions(p.id, p.id, 1, 1, p.active + p.bench, source=self), state)
        target = chosen[0]
        from packages.rules.zone_guards import hand_return_forbidden
        if hand_return_forbidden(p, state):
            return
        was_active = target in p.active
        (p.active if was_active else p.bench).remove(target)
        def return_card(card):
            for child in list(getattr(card, 'attachment', [])) + list(getattr(card, 'evolved', [])):
                return_card(child)
            fresh = type(card)()
            vars(card).clear()
            vars(card).update(vars(fresh))
            zone = p.hand if isinstance(card, PokemonCard) else p.discard
            card.cardPosition = CardPosition.HAND if isinstance(card, PokemonCard) else CardPosition.DISCARD
            zone.append(card)
            card.index = len(zone)
        return_card(target)
        for i, card in enumerate(p.bench):
            card.index = i + 1
        if was_active and p.bench:
            yield from _force_active_replacement(p, state, p.id)


class Ciphermaniac(TEF145CiphermaniacsCodebreaking):
    def __init__(self):
        super().__init__()
        from ptcg.core.enums import PokemonRule
        self.pokemonRule = PokemonRule.FUTURE

    def get_actions(self, state):
        p = current_player(state)
        return [UseSupporterAction(p.id, self)] if not p.supporterPlayedTurn and p.left else []

    def reduce_action(self, action, state):
        p = current_player(state)
        if not getattr(self, "copied_effect", False):
            move_cards(self, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state)
            p.supporterPlayedTurn = True
        chosen = yield from reduce_choose_card_actions(
            choose_card_actions(p.id, p.id, min(2, len(p.left)), min(2, len(p.left)), p.left[:], source=self), state)
        # Explicit in-flight zone so conservation and replay diagnostics remain
        # valid at the second (ordering) prompt.
        state.pending_cards = list(chosen)
        for c in chosen:
            p.left.remove(c)
        shuffle_cards(p.left, state)
        if len(chosen) == 1:
            p.left[:0] = chosen
            state.pending_cards = []
            chosen[0].cardPosition, chosen[0].index = CardPosition.LEFT, 1
            return
        top = yield from reduce_choose_card_actions(
            choose_card_actions(p.id, p.id, 1, 1, chosen, source=self), state)
        p.left[:0] = [top[0], next(c for c in chosen if c is not top[0])]
        state.pending_cards = []
        for i, c in enumerate(p.left):
            c.cardPosition, c.index = CardPosition.LEFT, i + 1


class Dudunsparce(TEF129Dudunsparce):
    def __init__(self):
        super().__init__()
        self.prize = 1

    def get_actions(self, state):
        result = []
        if self.position == PokemonPosition.ACTIVE:
            for attack in self.attacks:
                targets = opponent_active(state)
                if targets and check_energy(attack.cost, self.energy):
                    result.append(AttackAction(state.turn, self, attack, targets[0]))
        if not self.abilityUsed and current_player(state).left:
            result.extend(UseAbilityAction(state.turn, self, a) for a in self.ability)
        return result

    def _run_away_draw_ability(self, action, state):
        p = current_player(state)
        self.abilityUsed = True
        drawn = list(p.left[:3])
        move_cards(drawn, (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
        if not drawn:
            return
        was_active = self in p.active
        (p.active if was_active else p.bench).remove(self)
        for i, c in enumerate(p.bench):
            c.index = i + 1
        returned = []
        def flatten(card):
            returned.append(card)
            for c in list(getattr(card, 'attachment', [])) + list(getattr(card, 'evolved', [])):
                flatten(c)
            fresh = type(card)()
            vars(card).clear()
            vars(card).update(vars(fresh))
        flatten(self)
        p.left.extend(returned)
        shuffle_cards(p.left, state)
        for i, c in enumerate(p.left):
            c.cardPosition, c.index = CardPosition.LEFT, i + 1
        if was_active and p.bench:
            yield from _force_active_replacement(p, state, p.id)


from ptcg.cards.PAR.technical_machine_evolution import PAR178TechnicalMachineEvolution
from ptcg.core.action import UseToolAction, EvolvePokemonAction
from ptcg.core.attack import Attack
from ptcg.core.enums import CardType
from ptcg.core.reducer import reduce_evolve_pokemon_action
from ptcg.utils.utils import next_turn


class TechnicalMachine(PAR178TechnicalMachineEvolution):
    def __init__(self):
        super().__init__()
        self.text = 'Evolution: evolve up to two Benched Pokemon from deck. Discard this Tool at the end of your turn.'
        self.attacks = [Attack({'name':'Evolution','damage':0,'cost':[CardType.COLORLESS],'text':self.text})]

    def get_actions(self,state):
        p=current_player(state)
        if self in p.hand:
            return super().get_actions(state)
        holder=next((c for c in p.active if self in c.attachment),None)
        from packages.rules.modifiers import effective_attack_cost
        from packages.rules.attack_restrictions import allowed
        from packages.rules.tool_effects import enabled
        cost = effective_attack_cost(holder, self.attacks[0], self.attacks[0].cost, state) if holder else []
        if holder and enabled(state) and check_energy(cost,holder.energy) and allowed(holder,self.attacks[0],state) and not (p.firstTurn and p.id==state.starting_player):
            action=AttackAction(p.id,holder,self.attacks[0],holder)
            action.attack.cost=cost
            action.effect_source=self
            return [action]
        return []

    def reduce_action(self,action,state):
        if isinstance(action,UseToolAction):
            yield from super().reduce_action(action,state)
        elif isinstance(action,AttackAction):
            p=current_player(state)
            if p.bench:
                targets=yield from reduce_choose_card_actions(choose_card_actions(
                    p.id,p.id,1,min(2,len(p.bench)),list(p.bench),source=self),state)
                for target in targets:
                    candidates=[c for c in p.left if permitted(c) and getattr(c,'evolveFrom',[])[:1]==[target.name]]
                    if not candidates: continue
                    chosen=yield from reduce_choose_card_actions(choose_card_actions(
                        p.id,p.id,0,1,candidates,source=self),state)
                    if chosen:
                        evolution=chosen[0]
                        # Generic evolution reducer expects hand as source. No
                        # hand-play ability trigger is dispatched for this effect.
                        move_cards(evolution,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),state)
                        reduce_evolve_pokemon_action(EvolvePokemonAction(p.id,evolution,target),state)
            shuffle_cards(p.left,state)
            next_turn(state)


from ptcg.cards.TWM.drakloak import TWM129Drakloak
from ptcg.cards.TWM.munkidori import TWM095Munkidori
from ptcg.core.action import PassTurn
from ptcg.core.enums import SpecialCondition
from ptcg.core.reducer import _handle_knockout, reduce_attack_damage
from ptcg.utils.utils import opponent_player


class Drakloak(TWM129Drakloak):
    def get_actions(self,state):
        p=current_player(state)
        p.onceUsedTurn.setdefault('Recon Directive',False)
        actions=super().get_actions(state)
        return [a for a in actions if not isinstance(a,UseAbilityAction) or p.left]


class NumberOption(PassTurn):
    def __init__(self,p,value,label=None):
        super().__init__(p.id,p);self.value=value;self.label=label
    def to_dict(self):
        return {'actionType':'NumberOption','value':self.value, **({'label':self.label} if self.label else {})}


def choose_number(p,maximum,state):
    actions=[NumberOption(p,n,f'移动 {n} 个伤害指示物（{n * 10} 点）') for n in range(1,maximum+1)]
    chosen=yield (state.get_obs(p.id),0,False,{'raw_available_actions':actions})
    return chosen.value


class Munkidori(TWM095Munkidori):
    def get_actions(self,state):
        actions=[]
        p=current_player(state)
        if self.position==PokemonPosition.ACTIVE and opponent_active(state):
            for attack in self.attacks:
                if check_energy(attack.cost,self.energy):
                    actions.append(AttackAction(p.id,self,attack,opponent_active(state)[0]))
        if (not self.abilityUsed and any(energy_matches(e, "DARK") for e in self.energy)
                and any(maximum(c)>c.hp for c in p.active+p.bench)):
            actions.append(UseAbilityAction(p.id,self,self.ability[0]))
        return actions

    def reduce_action(self,action,state):
        p=current_player(state);opp=opponent_player(state)
        if isinstance(action,UseAbilityAction):
            sources=[c for c in p.active+p.bench if maximum(c)>c.hp]
            source=(yield from reduce_choose_card_actions(choose_card_actions(p.id,p.id,1,1,sources,source=self),state))[0]
            n=yield from choose_number(p,min(3,(maximum(source)-source.hp)//10),state)
            self.abilityUsed=True
            if n:
                target=(yield from reduce_choose_card_actions(choose_card_actions(p.id,p.id,1,1,opp.active+opp.bench,source=self),state))[0]
                source.hp+=n*10
                target.hp-=n*10
                if target.hp<=0:
                    yield from _handle_knockout(target,p,opp,state)
        elif isinstance(action,AttackAction):
            yield from reduce_attack_damage(action,state)
            from packages.rules.protection import blocked
            if action.target in opp.active+opp.bench and not blocked(action.target,state,'effects',action.source):
                action.target.special_condition=SpecialCondition.CONFUSED
            next_turn(state)
        else:
            yield from super().reduce_action(action,state)


from ptcg.cards.SFA.fezandipiti_ex import SFA092FezandipitiEX
from ptcg.cards.TWM.dragapult_ex import TWM200DragapultEX
from ptcg.cards.MEW.mew_ex import MEW151MewEX
from ptcg.core.ability_handler import trigger_attack_abilities
from ptcg.core.reducer import _calculate_damage


class Fezandipiti(SFA092FezandipitiEX):
    def get_actions(self, state):
        return [a for a in super().get_actions(state)
                if not isinstance(a, UseAbilityAction) or current_player(state).left]

    def reduce_action(self,action,state):
        if isinstance(action,AttackAction):
            p=current_player(state);opp=opponent_player(state)
            target=(yield from reduce_choose_card_actions(choose_card_actions(p.id,opp.id,1,1,opp.active+opp.bench,source=self),state))[0]
            action.target=target
            yield from reduce_attack_damage(action,state,apply_weakness_resistance=target in opp.active)
            next_turn(state)
        else:
            yield from super().reduce_action(action,state)


class Dragapult(TWM200DragapultEX):
    def __init__(self):
        super().__init__()
        # Tera is a Pokemon rule, not an Ability. Bench damage protection is
        # handled by core_fixes using pokemonRule, even with abilities suppressed.
        self.ability = []
        self.evolveFrom = ["Drakloak", "Dreepy"]
        self.evolved = []

    def reduce_action(self,action,state):
        if not isinstance(action,AttackAction) or action.attack.name!='Phantom Dive':
            yield from super().reduce_action(action,state)
            return
        p=current_player(state);opp=opponent_player(state)
        trigger_attack_abilities(action,state)
        from packages.rules.damage_events import deal, finish
        deal(action.source,action.target,_calculate_damage(action.source,action.target,action.attack.damage,state),state)
        for _ in range(6):
            if not opp.bench: break
            target=(yield from reduce_choose_card_actions(choose_card_actions(p.id,opp.id,1,1,list(opp.bench),source=self),state))[0]
            from packages.rules.protection import blocked
            if not blocked(target,state,'counters',action.source):target.hp-=10
        # Finish allocating the effect before checking any knockouts.
        if finish(state):
            from packages.rules.knockouts import resolve_group
            yield from resolve_group(state, damage_targets=[action.target])
        for target in list(opp.bench)+list(opp.active):
            if target.hp<=0 and target in opp.active+opp.bench:
                yield from _handle_knockout(target,p,opp,state,attack_damage=target is action.target)
        next_turn(state)


class Mew(MEW151MewEX):
    def get_actions(self, state):
        p = current_player(state)
        actions = []
        if self.position == PokemonPosition.ACTIVE and opponent_active(state) and check_energy(self.attacks[0].cost, self.energy):
            actions.append(AttackAction(p.id, self, self.attacks[0], opponent_active(state)[0]))
        if not self.abilityUsed and len(p.hand) < 3 and p.left:
            actions.append(UseAbilityAction(p.id, self, self.ability[0]))
        return actions

    def reduce_action(self, action, state):
        if isinstance(action, UseAbilityAction):
            p = current_player(state)
            move_cards(list(p.left[:max(0, 3-len(p.hand))]), (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
            self.abilityUsed = True
        else:
            yield from super().reduce_action(action, state)

    def _genome_hacking_attack(self, action, state):
        from packages.rules.copy_attacks import resolve
        yield from resolve({"kind": "copy_attack"}, action, state)

from ptcg.cards.PAF.iono import PAF080Iono
from ptcg.cards.PAL.superior_energy_retrieval import PAL189SuperiorEnergyRetrieval
from ptcg.cards.PAL.artazon import PAL171Artazon
from ptcg.cards.TEF.rescue_board import TEF159RescueBoard
from ptcg.core.action import UseItemAction, UseStadiumAction, PutStadiumAction
from ptcg.core.enums import EnergyType, Stage, PokemonRule, PokemonType
from ptcg.core.card import EnergyCard


class Iono(PAF080Iono):
    def get_actions(self, state):
        p = current_player(state)
        return super().get_actions(state) if len(p.hand) > 1 or opponent_player(state).hand else []

    def reduce_action(self, action, state):
        p, other = current_player(state), opponent_player(state)
        if not getattr(self, "copied_effect", False):
            move_cards(self, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state)
            p.supporterPlayedTurn = True
        any_returned = bool(p.hand or other.hand)
        for owner in (p, other):
            shuffle_cards(owner.hand, state)
            move_cards(list(owner.hand), (owner.id, CardPosition.HAND), (owner.id, CardPosition.LEFT), state)
        if any_returned:
            for owner in (p, other):
                move_cards(list(owner.left[:len(owner.prize)]), (owner.id, CardPosition.LEFT), (owner.id, CardPosition.HAND), state)


class SuperiorEnergyRetrieval(PAL189SuperiorEnergyRetrieval):
    @staticmethod
    def eligible(p):
        return [c for c in p.discard if isinstance(c, EnergyCard) and c.energyType == EnergyType.BASIC]

    def get_actions(self, state):
        p = current_player(state)
        return [UseItemAction(p.id, self)] if len(p.hand) >= 3 and self.eligible(p) else []

    def reduce_action(self, action, state):
        p = current_player(state)
        eligible = self.eligible(p)
        cost = yield from reduce_choose_card_actions(choose_card_actions(
            p.id, p.id, 2, 2, [c for c in p.hand if c is not self], source=self), state)
        move_cards(cost + [self], (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state)
        chosen = yield from reduce_choose_card_actions(choose_card_actions(
            p.id, p.id, 1, min(4, len(eligible)), eligible, source=self), state)
        move_cards(chosen, (p.id, CardPosition.DISCARD), (p.id, CardPosition.HAND), state)


class Artazon(PAL171Artazon):
    @staticmethod
    def eligible(p):
        return [c for c in p.left if isinstance(c, PokemonCard) and c.stage == Stage.BASIC
                and c.pokemonRule == PokemonRule.NONE and c.pokemonType == PokemonType.NORMAL
                and not getattr(c, 'isRadiant', False)]

    def get_actions(self, state):
        p = current_player(state)
        result = []
        if self in p.hand and not p.stadiumPlayedTurn and not any(c.name == self.name for c in state.stadium):
            result.append(PutStadiumAction(p.id, self))
        from packages.rules.stadiums import used
        if self in state.stadium and not used(self, state) and p.left and len(p.bench) < p.benchSize:
            result.append(UseStadiumAction(p.id, self))
        return result

    def reduce_action(self, action, state):
        if isinstance(action, UseStadiumAction):
            p = current_player(state)
            eligible = self.eligible(p)
            if eligible:
                chosen = yield from reduce_choose_card_actions(choose_card_actions(
                    p.id, p.id, 0, 1, eligible, source=self), state)
                move_cards(chosen, (p.id, CardPosition.LEFT), (p.id, CardPosition.BENCH), state)
                for card in chosen:
                    card.position = PokemonPosition.BENCH
                    card.firstTurnPlayed = True
            shuffle_cards(p.left, state)
            from packages.rules.stadiums import mark_used
            mark_used(self, state)
        else:
            yield from super().reduce_action(action, state)


class RescueBoard(TEF159RescueBoard):
    def use_ability(self, action, state):
        # Derived before legal actions; never double-apply or retain old holder.
        return

from ptcg.cards.TEF.prime_catcher import TEF157PrimeCatcher
from ptcg.utils.utils import switch_pokemon


class PrimeCatcher(TEF157PrimeCatcher):
    def __init__(self):
        super().__init__()
        self.aceSpec = True

    def get_actions(self, state):
        p = current_player(state)
        return [UseItemAction(p.id, self)] if opponent_player(state).bench else []

    def reduce_action(self, action, state):
        p, other = current_player(state), opponent_player(state)
        move_cards(self, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state)
        chosen = yield from reduce_choose_card_actions(choose_card_actions(
            p.id, other.id, 1, 1, list(other.bench), source=self), state)
        switch_pokemon(other.active[0], chosen[0], other)
        if p.bench:
            chosen = yield from reduce_choose_card_actions(choose_card_actions(
                p.id, p.id, 1, 1, list(p.bench), source=self), state)
            switch_pokemon(p.active[0], chosen[0], p)

# Historical English regression fixture; not added to the CN admission catalog.
from ptcg.cards.BRS.lumineon_v import BRS040LumineonV


class Lumineon(BRS040LumineonV):
    def reduce_action(self, action, state):
        if not isinstance(action, AttackAction):
            yield from super().reduce_action(action, state)
            return
        p = current_player(state)
        yield from reduce_attack_damage(action, state)
        physical = action.source
        if physical in p.active + p.bench:
            was_active = physical in p.active
            (p.active if was_active else p.bench).remove(physical)
            returned = []
            def flatten(card):
                returned.append(card)
                for child in list(getattr(card, 'attachment', [])) + list(getattr(card, 'evolved', [])):
                    flatten(child)
                fresh = type(card)()
                vars(card).clear()
                vars(card).update(vars(fresh))
            flatten(physical)
            p.left.extend(returned)
            shuffle_cards(p.left, state)
            for i, card in enumerate(p.left):
                card.cardPosition, card.index = CardPosition.LEFT, i + 1
            if was_active and p.bench:
                yield from _force_active_replacement(p, state, p.id)
        next_turn(state)

from packages.rules.energy_units import matches as energy_matches

from packages.rules.pokemon_replacement import permitted
