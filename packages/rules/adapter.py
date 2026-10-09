"""P0.1 JSON-only agent boundary and content-versioned deterministic replay."""
from pathlib import Path
from ptcg.core.enums import PlayerId
from ptcg.utils.load_deck import load_deck
from packages.engine_adapter.base import Adapter as BaseAdapter, digest, wire
from packages.rules.engine import RulesEngine


def implementation_id():
    root = Path(__file__).resolve().parents[2]
    files = sorted((root / 'packages/rules').glob('*.py'))
    files += [root / 'packages/engine_adapter/base.py', root / 'packages/engine_adapter/cn_format.py']
    files += [root / 'scripts/engine/build_engine.py', root / 'artifacts/engine/overlay-hashes.json', root / 'data/engine/catalog.json',
              root / 'rulesets/cn-standard-2026-09-16.json', root / 'data/cardpool/plain-pokemon.json']
    return digest({p.relative_to(root).as_posix(): p.read_text(encoding='utf-8') for p in files})


def freeze_deck(source):
    # Replay must not depend on a mutable filesystem deck file.
    cards = load_deck(source).cards
    return [f'1 {c.name} {c.set_name} {c.number}' for c in cards]


class Adapter(BaseAdapter):
    @staticmethod
    def _card(card):
        result = BaseAdapter._card(card)
        mechanic = getattr(card, 'compiled_rule', {}).get('mechanic', {})
        if mechanic.get('kind') == 'sync_self_recovery':
            result['recovery'] = {'amount': mechanic['amount'], 'cure': bool(mechanic.get('cure'))}
        if hasattr(card, 'get_info'):
            result.update(card.get_info())
        if hasattr(card, 'id'):
            result['id'] = card.id
        if hasattr(card, 'maximum_hp'):
            result['maximumHp'] = card.maximum_hp
        if hasattr(card, 'special_condition'):
            result['specialCondition'] = card.special_condition.name
        if hasattr(card, 'retreat_blocked_turn'):
            result['retreatBlocked'] = True
        if hasattr(card, 'attack_blocked_turn'):
            result['attackBlocked'] = True
        if getattr(card,'attack_locks',None):
            result['limitedAttacks']=sorted(card.attack_locks)
        if getattr(card,'attack_protection',None):
            result['attackProtection']={k:card.attack_protection[k] for k in ('damage','effects','source') if k in card.attack_protection}
        if getattr(card, 'poisoned', False):
            result['poisoned'] = True
            result['poisonDamage'] = getattr(card,'poison_damage',10)
        if getattr(card, 'burned', False):
            result['burned'] = True
        if hasattr(card, 'damage_shield'):
            result['attackDamageReduction'] = card.damage_shield['amount']
            if 'maximum' in card.damage_shield:
                result['preventAttackDamageAtMost'] = card.damage_shield['maximum']
        if hasattr(card, 'energy'):
            result['energy'] = [e.name for e in card.energy]
        if hasattr(card, 'attacks'):
            from packages.rules.pokemon_types import types
            result['effectiveTypes'] = sorted(t.name for t in types(card))
        return result

    def __init__(self, seed=0, deck1='gholdengo_ex', deck2='charizard_ex', provenance=None):
        self.config = dict(seed=seed, deck1=freeze_deck(deck1), deck2=freeze_deck(deck2))
        self.env = RulesEngine(**self.config, record_game=False)
        self.config['provenance'] = wire(provenance)
        self.obs, _, self.done, self.info = self.env.reset()
        self.version, self.commands, self.receipts, self.failed = 0, [], {}, False

    def view(self, viewer):
        if viewer not in (PlayerId.PLAYER1, PlayerId.PLAYER2):
            raise ValueError('INVALID_VIEWER')
        result = super().view(viewer)
        players = self.env.get_players()
        own = next(p for p in players if p.id == viewer)
        other = next(p for p in players if p.id != viewer)
        for role, player in (('self', own), ('opponent', other)):
            result['observation'][role]['faceUpPrizes'] = [dict(self._card(c), prizeIndex=i) for i, c in enumerate(player.prize) if getattr(c, 'prize_face_up', False)]
            for dto_zone, zone in (('discard', 'discard'), ('lost_zone', 'lostZone')):
                result['observation'][role][dto_zone] = [self._card(c) for c in getattr(player, zone)]
            result['observation'][role]['turnFlags'] = {
                'firstPersonalTurn': player.firstTurn,
                'energyPlayed': player.energyPlayedTurn,
                'supporterPlayed': player.supporterPlayedTurn,
                'itemsBlocked': getattr(player,'item_blocked_turn',None)==self.env.gamestate.turn_number,
                'retreated': player.retreatTurn}
            state = self.env.gamestate
            if getattr(player, 'attack_blocked_turn', None) == state.turn_number:
                result['observation'][role]['turnFlags']['attacksBlocked'] = True
            from packages.rules.abilities import enabled
            for zone in ('active', 'bench'):
                for dto, card in zip(result['observation'][role][zone], getattr(player, zone)):
                    if getattr(card, 'ability', []) and not enabled(card, state):
                        dto['abilitiesSuppressed'] = True
                    for attr, key in (('attack_damage_reduction', 'attackPowerReduction'), ('turn_damage_bonus', 'attackPowerBonus'), ('damage_retaliation', 'retaliationCounters')):
                        effect = getattr(card, attr, {})
                        if effect.get('turn') in (state.turn_number, state.turn_number + 1):
                            dto[key] = effect.get('amount', effect.get('counters', 0))
        result['observation']['self']['hand'] = [self._card(c) for c in own.hand]
        if self.env.start_stage:
            result['observation']['opponent']['active'] = []
            result['observation']['opponent']['bench'] = []
        provenance = self.config.get('provenance')
        if provenance:
            # Deck lists are server-private even though catalog metadata is public.
            result['catalogVersion'] = provenance['catalogVersion']
        result['phase'] = self.env.phase
        result['startingPlayer'] = (self.env.gamestate.starting_player.name
                                    if self.env.gamestate.starting_player else None)
        result['setupEvents'] = wire(self.env.public_setup_events)
        result['publicReveals'] = wire(self.env.gamestate.public_reveals)
        return result

    def submit(self, viewer, command):
        # Only whitelisted public search results are revealed. In particular,
        # Drakloak's private top-two choice and Ciphermaniac's ordering stay private.
        state = self.env.gamestate
        prompt = self.info.get('prompt')
        source_id = getattr(getattr(prompt, 'source', None), 'id', None)
        public_searches = {'PAR-163', 'OBF-186', 'PAF-091', 'PAL-189', 'SFA-061'}
        before = {p.id: set(map(id, p.hand)) for p in self.env.get_players()}
        old_version = self.version
        result = super().submit(viewer, command)
        if self.version != old_version and source_id in public_searches:
            for player in self.env.get_players():
                cards = [c for c in player.hand if id(c) not in before[player.id]]
                if cards:
                    state.public_reveals.append({'kind': 'search_reveal', 'actor': player.id.name,
                                                 'cards': [self._card(c) for c in cards]})
        return result

    def private_digest(self):
        return digest({'base': super().private_digest(), 'phase': self.env.phase,
                       'setupEvents': self.env.public_setup_events})

    def export_private_replay(self):
        return wire({'schema': 'p01-replay-v1', 'implementation': implementation_id(),
                     'baseCommit': '92c3cc4fe85a26f102d7bb6b3e8be7678512d2e5',
                     'config': self.config, 'commands': self.commands,
                     'stateDigest': self.private_digest()})

    @classmethod
    def replay(cls, record):
        if (record.get('schema') != 'p01-replay-v1'
                or record.get('implementation') != implementation_id()
                or record.get('baseCommit') != '92c3cc4fe85a26f102d7bb6b3e8be7678512d2e5'):
            raise ValueError('ENGINE_VERSION_MISMATCH')
        game = cls(**record['config'])
        for row in record['commands']:
            game.submit(PlayerId[row['viewer']], row['command'])
        if game.private_digest() != record['stateDigest']:
            raise ValueError('REPLAY_STATE_MISMATCH')
        return game
