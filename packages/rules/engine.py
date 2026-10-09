"""Scoped rule fixes over the pinned upstream, without global monkey patches.

Setup follows https://www.pokemon.cn/tcg-rules-howtoplay?pageName=basic_rules04/
The online protocol uses a seeded fair draw instead of physical rock-paper-scissors.
"""
from ptcg import PokemonTCG
from ptcg.core.action import PassTurn, AttackAction, UseAbilityAction, choose_card_actions
from ptcg.core.card import PokemonCard
from ptcg.core.enums import PlayerId, Stage, CardPosition, PokemonPosition
from ptcg.core.exceptions import InvalidDeckError
from ptcg.core.player import Player
from ptcg.core.state import State
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import move_cards, current_player, opponent_active, check_energy
from ptcg.cards.PAR.gholdengo_ex import PAR139Gholdengoex
from packages.rules.effects import Ciphermaniac, Dudunsparce, Turo, TechnicalMachine, Drakloak, Munkidori, Dragapult, Fezandipiti, Mew, Iono, SuperiorEnergyRetrieval, Artazon, RescueBoard, PrimeCatcher, Lumineon
from packages.rules.core_fixes import refresh_energy

# The upstream does not consistently mark ACE SPEC. Explicit reviewed registry IDs.
ACE_IDS = frozenset({'TEF-153', 'TEF-157', 'TWM-152', 'TWM-163', 'P01-003'})


class SetupOption(PassTurn):
    def __init__(self, player, kind, value):
        super().__init__(player.id, player)
        self.kind, self.value = kind, value

    def to_dict(self):
        return {'actionType': 'SetupOption', 'kind': self.kind, 'value': self.value}

    def to_nl(self):
        return f'{self.kind}: {self.value}'


class RulesPlayer(Player):
    def __repr__(self):
        return f'RulesPlayer({getattr(self, "id", None)})'

    def get_actions(self, state):
        if any(not (p.active or p.bench) for p in (state.player1, state.player2)):
            return []
        from packages.rules.maximum_hp import reconcile
        reconcile(state)
        for pokemon in self.active + self.bench:
            refresh_energy(pokemon)
            if any(c.id == "TEF-159" for c in pokemon.attachment) or getattr(pokemon, "board_retreat", False):
                pokemon.board_retreat = True
                base = list(type(pokemon)().retreat)
                from packages.rules.tool_effects import enabled as tools_enabled
                has_board = tools_enabled(state) and any(c.id == "TEF-159" for c in pokemon.attachment)
                pokemon.retreat = ([] if pokemon.hp <= 30 else base[1:]) if has_board else base
            from packages.rules.modifiers import refresh_costs
            refresh_costs(pokemon, state)
        actions = super().get_actions(state)
        from packages.rules.borrowed_attacks import actions as borrowed_actions
        actions.extend(borrowed_actions(self, state))
        from packages.rules.hand_events import bench_actions
        actions.extend(bench_actions(self, state))
        if self.stadiumUsedTurn:
            # Each physical Stadium has its own once-per-turn effect.
            for stadium in state.stadium:
                actions.extend(stadium.get_actions(state))
        from packages.rules.abilities import enabled
        actions = [a for a in actions if not isinstance(a, UseAbilityAction)
                   or (a.source in self.hand and any(r["kind"] == "hand_bench" for r in (getattr(a.source, "spec", None) or {}).get("abilities", [])))
                   or enabled(a.source, state)]
        from ptcg.core.action import UseItemAction, UseSupporterAction, PutStadiumAction, AttachEnergyAction, EvolvePokemonAction
        from ptcg.core.enums import PokemonType
        for target in self.active+self.bench:
            if not enabled(target,state):
                continue
            for rule in (getattr(target,"spec",None) or {}).get("abilities",[]):
                if rule["kind"] != "evolution_permission" or rule.get("activeOnly") and target not in self.active:
                    continue
                if rule.get('secondFirstTurn') and not (self.firstTurn and self.id != state.starting_player):
                    continue
                for card in self.hand:
                    if not isinstance(card,PokemonCard) or card.stage == Stage.BASIC:
                        continue
                    if rule.get("rainbow"):
                        eligible = not self.firstTurn and not target.firstTurnPlayed and card.pokemonType == PokemonType.EX and getattr(card,"evolveFrom",[])[:1] == ["Eevee"]
                    else:
                        eligible = getattr(card,"evolveFrom",[])[:1] == [target.name]
                    if eligible and not any(isinstance(a,EvolvePokemonAction) and a.source is card and a.target is target for a in actions):
                        actions.append(EvolvePokemonAction(self.id,card,target))
        if self.firstTurn and self.id == state.starting_player:
            for card in self.active:
                actions.extend(a for a in card.get_actions(state) if isinstance(a, AttackAction) and getattr(a.attack_template, "compiled_rule", {}).get("mechanic", {}).get("allowFirstTurn"))
            for card in self.hand:
                if getattr(card, 'spec', {}).get('mechanic', {}).get('allowFirstTurn'):
                    actions.extend(a for a in card.get_actions(state) if not any(type(b) is type(a) and b.source is a.source for b in actions))
        # Keep firstTurn true for evolution. Only the starting player is
        # forbidden to attack during their first turn.
        if self.firstTurn and self.id != state.starting_player:
            for card in self.active:
                actions.extend(a for a in card.get_actions(state)
                               if isinstance(a, AttackAction)
                               and not any(isinstance(b, AttackAction)
                                           and b.source is a.source
                                           and b.attack.name == a.attack.name for b in actions))
        if not self.left:
            no_empty_deck = {'PAF-087', 'PAF-084', 'PAF-091', 'TEF-144', 'OBF-186', 'PAR-163'}
            actions = [a for a in actions if getattr(a.source, 'id', None) not in no_empty_deck]
        from ptcg.core.enums import EnergyType
        actions = [a for a in actions if not (
            isinstance(a, UseItemAction) and getattr(self,'item_blocked_turn',None)==state.turn_number
            or isinstance(a, UseSupporterAction) and getattr(self,'supporter_blocked_turn',None)==state.turn_number
            or isinstance(a, PutStadiumAction) and getattr(self,'stadium_blocked_turn',None)==state.turn_number
            or isinstance(a, AttachEnergyAction) and getattr(self,'special_energy_blocked_turn',None)==state.turn_number and a.source.energyType==EnergyType.SPECIAL
            or isinstance(a, EvolvePokemonAction) and (getattr(self,'evolution_blocked_turn',None)==state.turn_number or getattr(a.target,'evolution_blocked_turn',None)==state.turn_number)
        )]
        from packages.rules.hand_locks import allowed as hand_allowed
        actions = [a for a in actions if hand_allowed(a,self,state)]
        from packages.rules.pokemon_replacement import permitted
        actions = [a for a in actions if not isinstance(a, EvolvePokemonAction) or permitted(a.source)]
        from ptcg.core.action import RetreatAction
        from packages.rules.attack_restrictions import allowed
        from packages.rules.tool_effects import enabled as tools_enabled
        from ptcg.core.card import ToolCard
        if not (self.firstTurn and self.id == state.starting_player):
            for card in self.bench:
                actions.extend(a for a in card.get_actions(state) if isinstance(a, AttackAction)
                               and getattr(a.attack_template, 'compiled_rule', {}).get('mechanic', {}).get('benchAttack')
                               and not any(isinstance(b, AttackAction) and b.source is a.source and b.attack.name == a.attack.name for b in actions))
        from packages.rules.modifiers import rules as modifiers
        actions = [a for a in actions if not isinstance(a, RetreatAction) or not any(r.get('retreatBlocked') for r in modifiers(a.active_pokemon,state))]
        if not tools_enabled(state):
            actions = [a for a in actions if not isinstance(a, (AttackAction, UseAbilityAction)) or not isinstance(getattr(a, 'effect_source', a.source), ToolCard)]
        actions = [a for a in actions if not isinstance(a, AttackAction) or allowed(a.source, a.attack_template, state)]
        from ptcg.core.enums import SpecialCondition
        actions = [a for a in actions if not (
            isinstance(a, RetreatAction) and isinstance(a.active_pokemon, __import__('packages.rules.setup_doll',fromlist=['DollPokemon']).DollPokemon) or
            isinstance(a, (AttackAction, RetreatAction)) and
            getattr(a.source if isinstance(a, AttackAction) else a.active_pokemon, 'special_condition', SpecialCondition.NONE)
            in (SpecialCondition.ASLEEP, SpecialCondition.PARALYZED)
        )]
        return [a for a in actions if not (
            isinstance(a, AttackAction) and getattr(a.source, 'attack_blocked_turn', None) == state.turn_number
        )]


class Gholdengo(PAR139Gholdengoex):
    def get_actions(self, state):
        actions = []
        if self.position == PokemonPosition.ACTIVE:
            for attack in self.attacks:
                targets = opponent_active(state)
                if targets and check_energy(attack.cost, self.energy):
                    actions.append(AttackAction(state.turn, self, attack, targets[0]))
        if not self.abilityUsed and current_player(state).left:
            actions.extend(UseAbilityAction(state.turn, self, a) for a in self.ability)
        return actions

    def _coin_bonus_ability(self, action, state):
        player = current_player(state)
        count = 2 if self.position == PokemonPosition.ACTIVE else 1
        move_cards(list(player.left[:count]), (player.id, CardPosition.LEFT),
                   (player.id, CardPosition.HAND), state)
        self.abilityUsed = True


class RulesEngine(PokemonTCG):
    def __init__(self, *args, **kwargs):
        from ptcg.utils import utils
        if not getattr(utils, 'RULES_OVERLAY', False):
            raise RuntimeError('Run scripts/engine/build_engine.py and set PYTHONPATH=runtime/engine:.')
        super().__init__(*args, **kwargs)

    def _load_decks(self):
        super()._load_decks()
        for deck in (self._deck1_cards, self._deck2_cards):
            if sum(c.id in ACE_IDS or getattr(c, 'aceSpec', False) for c in deck.cards) > 1:
                raise InvalidDeckError(['ACE_SPEC_LIMIT'])
            for i, card in enumerate(deck.cards):
                corrections = {'PAR-139': Gholdengo, 'TEF-145': Ciphermaniac, 'TEF-129': Dudunsparce,
                               'PAR-257': Turo, 'PAR-178': TechnicalMachine,
                               'TWM-129': Drakloak, 'TWM-095': Munkidori,
                               'TWM-200': Dragapult, 'SFA-092': Fezandipiti, 'MEW-151': Mew, 'PAF-080': Iono, 'PAL-189': SuperiorEnergyRetrieval,
                               'PAL-171': Artazon, 'TEF-159': RescueBoard, 'TEF-157': PrimeCatcher, 'BRS-040': Lumineon}
                if card.id in corrections:
                    deck.cards[i] = corrections[card.id]()

    def _init_game_state(self):
        players = [RulesPlayer(self._deck1_cards), RulesPlayer(self._deck2_cards)]
        self.gamestate = State(*players, rng=self.rng, invalid_action_policy='raise')
        for p, pid in zip(players, PlayerId):
            p.id = pid
            p.left = list(p.deck)
        self.winner, self.recorder = None, None
        self.cur_available_actions = []
        self.start_stage = True
        self.phase = 'choose_order'
        self.public_setup_events = []
        self.gamestate.starting_player = None
        self.gamestate.pending_cards = []
        self.gamestate.public_reveals = []
        self.gamestate.mulligans = {p.id.name: 0 for p in players}

    def _option(self, player, kind, values):
        actions = [SetupOption(player, kind, value) for value in values]
        chosen = yield (self.observe(player.id), 0, False, {'raw_available_actions': actions})
        return chosen.value

    @staticmethod
    def _basic(cards):
        from packages.rules.setup_doll import is_doll
        return [c for c in cards if isinstance(c, PokemonCard) and c.stage == Stage.BASIC or is_doll(c)]

    def _deal(self, player):
        self.rng.shuffle(player.deck)
        player.hand, player.left = list(player.deck[:7]), list(player.deck[7:])
        for zone in (CardPosition.HAND, CardPosition.LEFT):
            cards = player.hand if zone == CardPosition.HAND else player.left
            for i, card in enumerate(cards):
                card.cardPosition, card.index = zone, i + 1

    def _place(self, player, cards, zone):
        from packages.rules.setup_doll import enter
        for card in list(cards):
            enter(card)
            move_cards(card, (player.id, CardPosition.HAND), (player.id, zone), self.gamestate)
            card.position = PokemonPosition.ACTIVE if zone == CardPosition.ACTIVE else PokemonPosition.BENCH

    def _bench(self, player):
        basic = self._basic(player.hand)
        capacity = min(len(basic), player.benchSize - len(player.bench))
        if capacity:
            actions = choose_card_actions(player.id, player.id, 0, capacity, basic)
            cards = yield from reduce_choose_card_actions(actions, self.gamestate)
            self._place(player, cards, CardPosition.BENCH)

    def _run_start_stage(self):
        state = self.gamestate
        players = self.get_players()
        winner = self.rng.choice(players)
        self.public_setup_events.append({'kind': 'order_draw', 'winner': winner.id.name})
        first = yield from self._option(winner, 'choose_order', ['first', 'second'])
        starter = winner if first == 'first' else next(p for p in players if p != winner)
        state.starting_player = starter.id
        state.turn = starter.id
        for p in players:
            self._deal(p)
        self.phase = 'mulligan'
        while True:
            invalid = [p for p in players if not self._basic(p.hand)]
            if not invalid:
                break
            self.public_setup_events.append({'kind': 'mulligan', 'hands': {
                p.id.name: [c.to_dict() for c in p.hand] for p in invalid}})
            # Explicit acknowledgment makes the revealed failed hands observable
            # before reshuffling, including simultaneous failures.
            for p in players:
                yield from self._option(p, 'ack_mulligan', [True])
            for p in invalid:
                state.mulligans[p.id.name] += 1
                self._deal(p)
        self.phase = 'active'
        for p in players:
            self.phase = 'active'
            actions = choose_card_actions(p.id, p.id, 1, 1, self._basic(p.hand))
            cards = yield from reduce_choose_card_actions(actions, state)
            self._place(p, cards, CardPosition.ACTIVE)
            self.phase = 'bench'
            yield from self._bench(p)
        self.phase = 'prizes'
        for p in players:
            move_cards(list(p.left[:6]), (p.id, CardPosition.LEFT), (p.id, CardPosition.PRIZE), state)
        for p, other in ((players[0], players[1]), (players[1], players[0])):
            bonus = max(0, state.mulligans[other.id.name] - state.mulligans[p.id.name])
            if bonus:
                self.phase = 'mulligan_bonus'
                count = yield from self._option(p, 'mulligan_bonus', range(min(bonus, len(p.left)) + 1))
                move_cards(list(p.left[:count]), (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
                if count:
                    self.phase = 'bonus_bench'
                    yield from self._bench(p)
        self.start_stage = False
        self.phase = 'playing'
        state.turn = starter.id
        for p in players:
            p.supporterPlayedTurn = p.id == starter.id
        move_cards(starter.left[0], (starter.id, CardPosition.LEFT), (starter.id, CardPosition.HAND), state)

    def observe(self, viewer_id):
        obs = super().observe(viewer_id)
        # Do not forward free-form upstream diagnostic messages across trust boundary.
        obs['auto_events'] = []
        if self.start_stage:
            obs['opponent']['active'] = []
            obs['opponent']['bench'] = []
        return obs

    def _reduce_action(self):
        action = yield
        yield from self._apply_action(action)

    def _apply_action(self, action):
        from ptcg.core.action import UseItemAction, UseSupporterAction, UseToolAction, PutStadiumAction, UseStadiumAction
        kinds = {AttackAction: 'attack', UseAbilityAction: 'ability', UseItemAction: 'item', UseSupporterAction: 'supporter', UseToolAction: 'tool', PutStadiumAction: 'stadium', UseStadiumAction: 'stadium'}
        previous = getattr(self.gamestate, 'effect_context', None)
        self.gamestate.effect_context = {'kind': kinds.get(type(action)), 'owner': action.playerId, 'source': action.source, 'fromHand': action.source in current_player(self.gamestate).hand}
        try:
            yield from self._apply_effect(action)
        finally:
            if previous is None:
                del self.gamestate.effect_context
            else:
                self.gamestate.effect_context = previous

    def _apply_effect(self, action):
        from packages.rules.turn_interrupts import trainer_failed, EndTurnByAttachment
        if trainer_failed(action, self.gamestate):
            return
        from packages.rules.maximum_hp import settle
        from ptcg.core.enums import SpecialCondition, Coin
        from ptcg.core.reducer import _handle_knockout
        from ptcg.utils.utils import flip_coin, opponent_player, next_turn
        if isinstance(action, AttackAction) and getattr(action.source, 'special_condition', None) == SpecialCondition.CONFUSED:
            if flip_coin(self.gamestate) == Coin.TAIL:
                p=current_player(self.gamestate)
                action.source.hp -= getattr(action.source,'confusion_damage',30)
                if action.source.hp <= 0:
                    yield from _handle_knockout(action.source, opponent_player(self.gamestate), p, self.gamestate)
                next_turn(self.gamestate)
                from packages.rules.end_phase import finish_pending
                yield from finish_pending(self.gamestate)
                return
        if isinstance(action, AttackAction):
            action.source.resolving_attack_name = action.attack.name
            check = getattr(action.source,'attack_coin_check',{})
            if check.get('turn') == self.gamestate.turn_number:
                succeeds = all([flip_coin(self.gamestate) == Coin.HEAD for _ in range(check['count'])])
                if not succeeds:
                    next_turn(self.gamestate)
                    from packages.rules.end_phase import finish_pending
                    yield from finish_pending(self.gamestate)
                    return
        if not isinstance(action.source, Player):
            current_player(self.gamestate).record_action(action)
        from ptcg.core.action import UseSupporterAction
        if isinstance(action, UseSupporterAction) and 'Team Rocket' in action.source.name:
            current_player(self.gamestate).rocket_supporter_turn = self.gamestate.turn_number
        source = getattr(action, 'effect_source', action.source)
        if isinstance(action, AttackAction):
            from packages.rules.attack_attachments import begin
            begin(action, self.gamestate)
            from packages.rules.history import attack_used
            attack_used(action, self.gamestate)
        from ptcg.core.action import EvolvePokemonAction
        if isinstance(action, EvolvePokemonAction):
            from packages.rules.hand_events import capture
            # Only the actual hand evolution action triggers this; deck
            # evolution effects may temporarily pass through the hand zone.
            capture(action.source, current_player(self.gamestate), self.gamestate, "evolution")
        import inspect
        from packages.rules.festival import candidate, second
        festival = candidate(action)
        if festival:
            self.gamestate.festival_attack_pending = True
        interrupted = False
        try:
            if getattr(action, 'borrowed_attack', False):
                from packages.rules.borrowed_attacks import resolve as borrowed_resolve
                yield from borrowed_resolve(source, action, self.gamestate)
            elif inspect.isgeneratorfunction(source.reduce_action):
                yield from source.reduce_action(action, self.gamestate)
            else:
                source.reduce_action(action, self.gamestate)
        except EndTurnByAttachment:
            interrupted = True
        yield from settle(self.gamestate)
        if interrupted:
            next_turn(self.gamestate)
        if festival:
            self.gamestate.festival_attack_pending = False
            followup = yield from second(action, self.gamestate)
            if followup:
                yield from self._apply_action(followup)
                return
            next_turn(self.gamestate)
        if getattr(self.gamestate, 'pending_event_turn', False):
            del self.gamestate.pending_event_turn
            next_turn(self.gamestate)
        if getattr(self.gamestate, 'pending_hp_turn', False):
            del self.gamestate.pending_hp_turn
            next_turn(self.gamestate)
        from packages.rules.end_phase import finish_pending
        yield from finish_pending(self.gamestate)
