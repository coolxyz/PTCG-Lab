"""Physical Special Energy, shared hand-attachment triggers and live supply."""

from ptcg.core.card import EnergyCard
from ptcg.core.action import AttachEnergyAction
from ptcg.core.enums import (
    CardType,
    EnergyType,
    Stage,
    PokemonType,
    PokemonRule,
    CardPosition,
)
from ptcg.utils.utils import (
    current_all_pokemon,
    can_attach_energy,
    move_cards,
    switch_pokemon,
)
from ptcg.core.reducer import reduce_attach_energy_action


class CompiledEnergy(EnergyCard):
    spec = None

    def __init__(self):
        super().__init__()
        self.id = self.spec["effectKey"]
        self.set_name, self.number = self.id.split("-", 1)
        self.name, self.text = self.spec["name"], self.spec["text"]
        self.energyType, self.cardType = EnergyType.SPECIAL, CardType.COLORLESS
        self.aceSpec = self.spec.get("aceSpec", False)
        self.provides = [CardType[self.spec["mechanic"]["provides"]]]

    def effective_provides(self, target, state=None):
        r = self.spec["mechanic"]
        if r.get("rocket"):
            return [CardType.PSYCHIC_DARK] * 2 if target.name.startswith("Team Rocket's ") else []
        if r.get("luminous") and any(
            c is not self
            and isinstance(c, EnergyCard)
            and c.energyType == EnergyType.SPECIAL
            for c in target.attachment
        ):
            return [CardType.COLORLESS]
        if r.get("reversal"):
            if state is None:
                return self.provides
            owner = next(
                (
                    p
                    for p in (state.player1, state.player2)
                    if target in p.active + p.bench
                ),
                None,
            )
            other = state.player2 if owner is state.player1 else state.player1
            if (
                owner
                and len(owner.prize) > len(other.prize)
                and target.stage != Stage.BASIC
                and target.pokemonType == PokemonType.NORMAL
                and target.pokemonRule not in (PokemonRule.RADIANT, PokemonRule.TERA)
            ):
                return [CardType.ANY] * 3
        return [CardType[r["provides"]]]

    def get_actions(self, state):
        return (
            [
                AttachEnergyAction(state.turn, self, c)
                for c in current_all_pokemon(state)
                if not self.spec["mechanic"].get("rocket") or c.name.startswith("Team Rocket's ")
            ]
            if can_attach_energy(state)
            else []
        )

    def reduce_action(self, action, state):
        if isinstance(action, AttachEnergyAction):
            reduce_attach_energy_action(action, state)
            from packages.rules.maximum_hp import reconcile

            reconcile(state)


def clear_conditions(target):
    immune = {
        s
        for e in target.attachment
        for s in (getattr(e, "spec", None) or {})
        .get("mechanic", {})
        .get("statusImmunity", [])
    }
    condition = getattr(target, "special_condition", None)
    if condition is not None and condition.name in immune:
        del target.special_condition


def attached(cards, source_pos, target_pos, state):
    if target_pos[1] not in (
        CardPosition.ACTIVE_ATTACHMENT,
        CardPosition.BENCH_ATTACHMENT,
    ):
        return
    owner = state.player1 if target_pos[0] == state.player1.id else state.player2
    target = next(
        (
            c
            for c in owner.active + owner.bench
            if any(e in c.attachment for e in cards)
        ),
        None,
    )
    if target is None:
        return
    from ptcg.core.card import ToolCard
    if source_pos[1] == CardPosition.HAND and any(isinstance(c, ToolCard) for c in cards):
        target.hand_tool_turn = state.turn_number
    if source_pos[1] == CardPosition.HAND:
        from packages.rules.hand_events import capture
        for card in cards:
            if isinstance(card, EnergyCard):
                capture(target, owner, state, "energy")
        if any(isinstance(c, EnergyCard) for c in cards) and getattr(target, "attachment_ends_turn", None) == state.turn_number:
            from packages.rules.turn_interrupts import EndTurnByAttachment
            raise EndTurnByAttachment
    for card in cards:
        if not isinstance(card, CompiledEnergy):
            continue
        r = card.spec["mechanic"]
        clear_conditions(target)
        if source_pos[1] != CardPosition.HAND:
            continue
        if r.get("switchFromHand") and target in owner.bench and owner.active:
            switch_pokemon(owner.active[0], target, owner)
        if r.get("healFromHand"):
            from packages.rules.maximum_hp import maximum

            target.hp = healed(target, r["healFromHand"], state, record=True)
        if r.get("drawFromHand"):
            move_cards(
                list(owner.left[: r["drawFromHand"]]),
                (owner.id, CardPosition.LEFT),
                (owner.id, CardPosition.HAND),
                state,
            )


def install(namespace, specs):
    for spec in specs:
        name = "SpecialEnergy" + spec["effectKey"].split("-", 1)[1]
        namespace[name] = type(
            name, (CompiledEnergy,), {"spec": spec, "__module__": namespace["__name__"]}
        )

from packages.rules.healing import value as healed
