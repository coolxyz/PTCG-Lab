"""Snorlax Doll is an Item outside play and a Pokemon only after setup."""
from ptcg.core.card import PokemonCard
from ptcg.core.enums import CardType, Stage, PokemonType, PokemonRule, CardPosition, PokemonPosition
from ptcg.core.action import PassTurn
from ptcg.core.reducer import _force_active_replacement
from ptcg.utils.utils import current_player


def is_doll(card):
    return (getattr(card, "spec", None) or {}).get("mechanic", {}).get("kind") == "setup_doll"


class DiscardDoll(PassTurn):
    def to_nl(self):
        return "将场上的这张卡牌放于弃牌区"


class DollPokemon(PokemonCard):
    spec = None
    item_class = None

    def __init__(self):
        super().__init__()
        self.id, self.name = self.spec["effectKey"], self.spec["name"]
        self.set_name, self.number = self.id.split("-", 1)
        fossil = self.spec['mechanic']['kind'] == 'fossil'
        self.hp, self.prize, self.stage, self.cardType = (60 if fossil else 120), (1 if fossil else 0), Stage.BASIC, CardType.COLORLESS
        self.pokemonType, self.pokemonRule = PokemonType.NORMAL, PokemonRule.NONE
        self.weakness, self.resistance, self.retreat = [], [], []
        self.attacks, self.ability, self.energy, self.evolved, self.attachment, self.evolveFrom = [], [], [], [], [], []
        self.text = self.spec["text"]
        if fossil:
            from packages.rules.abilities import initialize
            initialize(self)

    def get_actions(self, state):
        p = current_player(state)
        return [DiscardDoll(p.id, self)] if self in p.active + p.bench else []

    def reduce_action(self, action, state):
        from packages.rules.core_fixes import discard_pokemon
        from ptcg.core.action import EvolvePokemonAction
        if isinstance(action,EvolvePokemonAction):
            from ptcg.core.reducer import reduce_evolve_pokemon_action
            reduce_evolve_pokemon_action(action,state)
            return
        p = current_player(state)
        if isinstance(action, DiscardDoll):
            discard_pokemon(p, self)
            normalize(state)
            if not p.active and p.bench:
                yield from _force_active_replacement(p, state, p.id)


def install(item_class):
    from packages.rules.trainers import CompiledTrainer
    name = "InPlay" + item_class.__name__
    globals()[name] = type(name, (CompiledTrainer, DollPokemon), {"__module__": __name__, "spec": item_class.spec, "item_class": item_class, "__init__": DollPokemon.__init__, "get_actions": DollPokemon.get_actions, "reduce_action": DollPokemon.reduce_action})


def enter(card):
    if (is_doll(card) or (getattr(card,'spec',None) or {}).get('mechanic',{}).get('kind')=='fossil') and not isinstance(card, DollPokemon):
        cls = globals()["InPlay" + type(card).__name__]
        card.__class__ = cls
        card.__init__()


def leave(card):
    if isinstance(card, DollPokemon):
        position, index = card.cardPosition, card.index
        card.__class__ = card.item_class
        card.__init__()
        card.cardPosition, card.index = position, index


def normalize(state):
    for owner in (state.player1, state.player2):
        for card in owner.hand + owner.left + owner.discard + owner.prize:
            leave(card)
