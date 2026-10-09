"""Closed, source-compiled ordinary Pokemon and basic energy mechanisms."""
from packages.rules.maximum_hp import maximum


import json
from pathlib import Path
from ptcg.core.action import (
    AttackAction,
    PlayPokemonAction,
    AttachEnergyAction,
    EvolvePokemonAction,
    UseAbilityAction,
    choose_card_actions,
)
from ptcg.core.attack import Attack
from ptcg.core.card import PokemonCard, EnergyCard
from ptcg.core.enums import (
    CardType,
    EnergyType,
    PokemonType,
    PokemonRule,
    Stage,
    PokemonPosition,
    Coin,
    CardPosition,
)
from ptcg.core.reducer import (
    reduce_attack_action,
    reduce_play_pokemon_action,
    reduce_attach_energy_action,
    reduce_evolve_pokemon_action,
    reduce_attack_damage,
    reduce_choose_card_actions,
)
from ptcg.utils.utils import (
    check_energy,
    opponent_active,
    can_attach_energy,
    current_all_pokemon,
    flip_coin,
    next_turn,
    current_player,
    move_cards,
    opponent_player,
    switch_pokemon,
)
from packages.rules.zone_effects import KINDS as ZONE_KINDS, resolve as resolve_zone
from packages.rules.field_effects import KINDS as FIELD_KINDS, resolve as resolve_field

PATH = Path(__file__).resolve().parents[2] / "data/cardpool/plain-pokemon.json"
SPECS = json.loads(PATH.read_text(encoding="utf-8"))["cards"]
TRAINER_SPECS = json.loads(PATH.read_text(encoding="utf-8")).get("trainers", [])
SPECIAL_ENERGY_SPECS = json.loads(PATH.read_text(encoding="utf-8")).get("specialEnergies", [])
ENERGIES = {
    "001": ("Grass", "GRASS"),
    "003": ("Water", "WATER"),
    "004": ("Lightning", "LIGHTNING"),
    "006": ("Fighting", "FIGHTING"),
    "007": ("Darkness", "DARK"),
}


class PlainPokemon(PokemonCard):
    spec = None

    def __init__(self):
        super().__init__()
        s = self.spec
        self.id, self.set_name, self.number = (
            s["effectKey"],
            "P4P",
            s["effectKey"].split("-", 1)[1],
        )
        self.name, self.hp, self.cardType = s["name"], s["hp"], CardType[s["type"]]
        self.stage, self.pokemonType, self.pokemonRule = (
            Stage[s["stage"]],
            PokemonType[s.get("pokemonType", "NORMAL")],
            PokemonRule[s.get("pokemonRule", "NONE")],
        )
        self.evolveFrom = list(s["evolvesFrom"])
        self.prize = s.get("prize", 1)
        self.retreat = [CardType.COLORLESS] * s["retreat"]
        self.weakness = [CardType[x] for x in s["weakness"]]
        self.resistance = [CardType[x] for x in s["resistance"]]
        self.energy, self.attachment, self.evolved, self.ability = [], [], [], []
        from packages.rules.abilities import initialize

        initialize(self)
        self.attacks = [
            Attack(
                {
                    "name": a["name"],
                    "damage": a["damage"],
                    "cost": [CardType[x] for x in a["cost"]],
                    "text": a.get("text", ""),
                }
            )
            for a in s["attacks"]
        ]
        for attack, definition in zip(self.attacks, s["attacks"]):
            attack.compiled_rule = definition

    def get_actions(self, state):
        from packages.rules.abilities import available
        from packages.rules.attack_restrictions import allowed

        abilities = available(self, state)
        targets = opponent_active(state)
        if (
            not targets
            or getattr(self, "attack_blocked_turn", None) == state.turn_number
        ):
            return abilities
        return abilities + [
            AttackAction(state.turn, self, a, targets[0])
            for a in self.attacks
            if (self.position == PokemonPosition.ACTIVE or getattr(a, 'compiled_rule', {}).get('mechanic', {}).get('benchAttack'))
            and check_energy(a.cost, self.energy) and allowed(self, a, state)
        ]

    def reduce_action(self, action, state):
        if isinstance(action, PlayPokemonAction):
            from_hand = self in current_player(state).hand
            reduce_play_pokemon_action(action, state)
            if from_hand and self in current_player(state).bench:
                from packages.rules.entry_effects import resolve

                yield from resolve(self, "bench", state)
        elif isinstance(action, EvolvePokemonAction):
            from_hand = self in current_player(state).hand
            reduce_evolve_pokemon_action(action, state)
            from packages.rules.maximum_hp import reconcile

            reconcile(state)
            if from_hand:
                from packages.rules.entry_effects import resolve

                yield from resolve(self, "evolve", state)
        elif isinstance(action, UseAbilityAction):
            from packages.rules.abilities import resolve

            yield from resolve(self, action, state)
        elif isinstance(action, AttackAction):
            from packages.rules.attack_attachments import begin
            begin(action, state)
            action.source.resolving_attack_name = getattr(action, "declared_name", action.attack.name)
            action.source.resolving_attack_cost = list(action.attack.cost)
            definition = self.spec["attacks"][
                self.attacks.index(action.attack_template)
            ]
            mechanic = definition.get("mechanic")
            if mechanic and mechanic["kind"] == "advanced_attack":
                from packages.rules.advanced_attacks import resolve
                yield from resolve(mechanic, action, state)
                return
            if mechanic and mechanic["kind"] == "attack_contract":
                from packages.rules.attack_contracts import permitted
                if permitted(mechanic, action.source, current_player(state), state):
                    def after(action, state):
                        for child in mechanic.get("effects", []):
                            yield from PlainPokemon.resolve_mechanic(action.source, child, action, state)
                    yield from reduce_attack_damage(action, state, after_damage=after)
                next_turn(state)
                return
            if mechanic and mechanic["kind"] == "coin_branch":
                from packages.rules.coin_branches import resolve
                yield from resolve(mechanic, action, state)
                return
            if mechanic and mechanic["kind"] == "conditional_attack":
                from packages.rules.attack_math import damage as expression_damage
                rule = mechanic["condition"]
                count = expression_damage({**rule, "mode": "multiply", "factor": 1}, action, state)
                succeeds = count < rule["lessThan"] if "lessThan" in rule else count in rule["in"] if "in" in rule else count > 0
                if succeeds:
                    yield from reduce_attack_action(action, state)
                else:
                    next_turn(state)
                return
            if mechanic and mechanic["kind"] == "attack_extra_prize":
                action.bonus_prizes = mechanic["count"]
                yield from reduce_attack_action(action, state)
                return
            if mechanic and mechanic["kind"] == "staged_attack":
                from packages.rules.staged_attacks import resolve
                yield from resolve(mechanic, action, state)
                return
            if mechanic and mechanic["kind"] == "discard_damage":
                from packages.rules.discard_damage import resolve

                yield from resolve(mechanic, action, state)
                return
            if mechanic and mechanic["kind"] == "copy_attack":
                from packages.rules.copy_attacks import resolve
                yield from resolve(mechanic, action, state)
                return
            if mechanic and mechanic["kind"] == "discard_tools_before_damage":
                from ptcg.core.card import ToolCard
                from packages.rules.zone_effects import discard_attached

                from packages.rules.protection import blocked

                discard_attached(
                    action.target,
                    [
                        c
                        for c in action.target.attachment
                        if isinstance(c, ToolCard)
                        and not blocked(action.target, state, "effects", action.source)
                    ],
                    opponent_player(state),
                )
                yield from reduce_attack_action(action, state)
                return
            if mechanic and mechanic["kind"] == "target_damage":
                from packages.rules.target_damage import resolve

                yield from resolve(mechanic, action, state)
                return
            if mechanic and mechanic["kind"] == "sequence":
                steps = mechanic["steps"]
                if steps[0]["kind"] == "damage_expression":
                    from packages.rules.attack_math import damage

                    action.attack.damage = damage(steps[0], action, state)
                if action.attack.damage:
                    yield from reduce_attack_damage(
                        action, state, after_damage=self.after_damage
                    )
                else:
                    yield from self.after_damage(action, state)
                    from packages.rules.knockouts import resolve_group

                    yield from resolve_group(
                        state,
                        damage_targets=getattr(action, "group_damage_targets", []),
                    )
                next_turn(state)
                return
            if mechanic and mechanic["kind"] == "ignore_damage_modifiers":
                ignore = mechanic["ignore"]
                yield from reduce_attack_damage(
                    action,
                    state,
                    apply_weakness_resistance=ignore not in ("all", "weakness_resistance"),
                    ignore_effects=ignore in ("effects", "all"),
                    ignore_resistance=ignore == "resistance",
                )
                next_turn(state)
                return
            if mechanic and mechanic["kind"] == "damage_expression":
                from packages.rules.attack_math import damage

                if mechanic.get("revealHand"):
                    from packages.rules.entry_effects import reveal
                    reveal(list(opponent_player(state).hand),state,opponent_player(state))
                action.attack.damage = damage(mechanic, action, state)
                if mechanic.get("ignoreWeakness") and action.attack.damage:
                    yield from reduce_attack_damage(action,state,ignore_weakness=True)
                    next_turn(state)
                    return
                if action.attack.damage:
                    yield from reduce_attack_action(action, state)
                else:
                    next_turn(state)
                return
            if mechanic and mechanic["kind"] in ZONE_KINDS | FIELD_KINDS:
                if definition["damage"]:
                    yield from reduce_attack_damage(
                        action, state, after_damage=self.after_damage
                    )
                else:
                    yield from self.after_damage(action, state)
                    if hasattr(action, "group_damage_targets"):
                        from packages.rules.knockouts import resolve_group

                        yield from resolve_group(
                            state, damage_targets=action.group_damage_targets
                        )
                next_turn(state)
                return
            if (
                mechanic
                and mechanic["kind"] == "damage_shield"
                and definition["damage"] == 0
            ):
                yield from self.after_damage(action, state)
                next_turn(state)
                return
            if mechanic and mechanic["kind"] == "draw" and definition["damage"] == 0:
                player = current_player(state)
                move_cards(
                    list(player.left[: mechanic["count"]]),
                    (player.id, CardPosition.LEFT),
                    (player.id, CardPosition.HAND),
                    state,
                )
                next_turn(state)
                return
            if mechanic and mechanic["kind"] in (
                "sync_self_recovery",
                "prevent_retreat",
                "coin_discard_energy",
                "discard_typed_energy",
                "opponent_switch",
                "draw",
                "damage_shield",
                "attack_lock",
                "opponent_attack_lock",
                "drain_damage",
                "named_attack_lock",
                "special_status",
                "recoil",
            ):
                yield from reduce_attack_damage(
                    action, state, after_damage=self.after_damage
                )
                next_turn(state)
                return
            if mechanic and mechanic["kind"] in ("coin_count", "coin_until_tails"):
                heads = 0
                if mechanic["kind"] == "coin_count":
                    count = mechanic.get("count", 0)
                    if mechanic.get("countTerm"):
                        from packages.rules.attack_math import damage as expression_damage
                        count = expression_damage({**mechanic, "term": mechanic["countTerm"], "factor": 1, "mode": "multiply"}, action, state)
                    heads = sum(
                        flip_coin(state) == Coin.HEAD for _ in range(count)
                    )
                else:
                    while flip_coin(state) == Coin.HEAD:
                        heads += 1
                action.attack.damage = heads * mechanic["perHead"] + (
                    action.attack.damage if mechanic.get("mode") == "add" else 0
                )
                if not action.attack.damage:
                    next_turn(state)
                    return
                yield from reduce_attack_action(action, state)
                return
            if mechanic:
                result = flip_coin(state)
                if mechanic["kind"] == "coin_fail" and result == Coin.TAIL:
                    state.auto_events.append(f"{action.attack.name} failed.")
                    next_turn(state)
                    return
                if mechanic["kind"] == "coin_bonus" and result == Coin.HEAD:
                    action.attack.damage += mechanic["bonus"]
            yield from reduce_attack_action(action, state)

    def after_damage(self, action, state):
        mechanic = self.spec["attacks"][self.attacks.index(action.attack_template)][
            "mechanic"
        ]
        if mechanic["kind"] == "sequence":
            for step in mechanic["steps"]:
                if step["kind"] != "damage_expression":
                    yield from self.resolve_mechanic(step, action, state)
            return
        yield from self.resolve_mechanic(mechanic, action, state)

    def resolve_mechanic(self, mechanic, action, state):
        kind = mechanic["kind"]
        from packages.rules.protection import blocked

        if kind in (
            "prevent_retreat",
            "opponent_attack_lock",
            "opponent_switch",
            "discard_typed_energy",
        ) and blocked(action.target, state, "effects", action.source):
            return
        if kind == "sync_self_recovery":
            action.source.hp = healed(action.source, mechanic["amount"], state, record=True)
            if mechanic.get("cure"):
                for attr in ("special_condition", "poisoned", "burned", "poison_damage", "sleep_coins", "confusion_damage"):
                    if hasattr(action.source, attr):
                        delattr(action.source, attr)
        elif kind == "recoil":
            from packages.rules.damage_events import deal
            from packages.rules.core_fixes import shield_damage
            deal(action.source, action.source, shield_damage(action.source, mechanic["amount"], state, action.source), state)
        elif kind == "special_status":
            from ptcg.core.enums import SpecialCondition

            if not mechanic["coin"] or flip_coin(state) == Coin.HEAD:
                targets = (
                    [action.source, action.target]
                    if mechanic["target"] == "both"
                    else [
                        action.source if mechanic["target"] == "self" else action.target
                    ]
                )
                for target in targets:
                    if target is action.target and blocked(
                        target, state, "effects", action.source
                    ):
                        continue
                    for status in mechanic.get("statuses", [mechanic["status"]]):
                        if status in ("POISONED", "BURNED"):
                            setattr(target, status.lower(), True)
                            if status == "POISONED":
                                target.poison_damage = mechanic.get("poisonDamage", 10)
                        else:
                            from packages.rules.status_immunity import apply_status
                            apply_status(target, SpecialCondition[status])
                            if status == "ASLEEP":
                                target.sleep_coins = mechanic.get("sleepCoins",1)
                            if status == "CONFUSED":
                                target.confusion_damage = mechanic.get("confusionDamage",30)
                    from packages.rules.maximum_hp import reconcile
                    reconcile(state)
        elif kind == "attack_lock":
            if not mechanic.get("coinTails") or flip_coin(state) == Coin.TAIL:
                action.source.attack_blocked_turn = state.turn_number + 2
        elif kind == "opponent_attack_lock":
            action.target.attack_blocked_turn = state.turn_number + 1
        elif kind == "drain_damage":
            action.source.hp = healed(action.source, action.damage_dealt, state, record=True)
        elif kind == "named_attack_lock":
            locks = getattr(action.source, "attack_locks", {})
            locks[mechanic["name"]] = (
                "active" if mechanic["duration"] == "active" else state.turn_number + 2
            )
            action.source.attack_locks = locks
        elif kind in ZONE_KINDS:
            yield from resolve_zone(mechanic, action, state)
        elif kind in FIELD_KINDS:
            yield from resolve_field(mechanic, action, state)
        elif kind == "damage_shield":
            action.source.damage_shield = {
                **({'maximum':mechanic['maximum']} if 'maximum' in mechanic else {}),
                "turn": state.turn_number + 1,
                "amount": mechanic["amount"],
            }
        elif kind == "draw":
            player = current_player(state)
            move_cards(
                list(player.left[: mechanic["count"]]),
                (player.id, CardPosition.LEFT),
                (player.id, CardPosition.HAND),
                state,
            )
        elif kind == "prevent_retreat":
            action.target.retreat_blocked_turn = state.turn_number + 1
        elif kind == "opponent_switch":
            owner = opponent_player(state)
            if owner.bench:
                chosen = yield from reduce_choose_card_actions(
                    choose_card_actions(
                        owner.id,
                        owner.id,
                        1,
                        1,
                        list(owner.bench),
                        source=action.source,
                        tips="Choose your new Active Pokemon.",
                    ),
                    state,
                )
                switch_pokemon(action.target, chosen[0], owner)
        elif kind in ("discard_typed_energy", "coin_discard_energy"):
            if kind == "coin_discard_energy" and flip_coin(state) != Coin.HEAD:
                return
            target = action.target
            if blocked(target, state, "effects", action.source):
                return
            energies = [c for c in target.attachment if isinstance(c, EnergyCard)]
            if kind == "discard_typed_energy":
                from packages.rules.core_fixes import refresh_energy

                refresh_energy(target)
                energies = [
                    c
                    for c in energies
                    if any(energy_matches(e, mechanic["type"]) for e in c.provides)
                ]
            if energies:
                chosen = yield from reduce_choose_card_actions(
                    choose_card_actions(
                        state.turn,
                        state.turn,
                        1,
                        1,
                        energies,
                        source=action.source,
                        tips="Choose an Energy attached to the Defending Pokemon to discard.",
                    ),
                    state,
                )
                from packages.rules.core_fixes import discard_card, refresh_energy

                owner = opponent_player(state)
                for card in chosen:
                    target.attachment.remove(card)
                    discard_card(owner, card)
                # Recompute special-energy conditions after removing a physical card.
                target.energy = [
                    e
                    for c in target.attachment
                    if isinstance(c, EnergyCard)
                    for e in c.provides
                ]
                refresh_energy(target)
                for i, card in enumerate(target.attachment):
                    card.index = i + 1
        else:
            raise ValueError("Unknown attack mechanic: " + kind)


class BasicEnergy(EnergyCard):
    number = None

    def __init__(self):
        super().__init__()
        name, kind = ENERGIES[self.number]
        self.set_name, self.id, self.name = (
            "P4E",
            "P4E-" + self.number,
            name + " Energy",
        )
        self.cardType, self.energyType = CardType[kind], EnergyType.BASIC
        self.provides = [self.cardType]

    def get_actions(self, state):
        return (
            [
                AttachEnergyAction(state.turn, self, p)
                for p in current_all_pokemon(state)
            ]
            if can_attach_energy(state)
            else []
        )

    def reduce_action(self, action, state):
        if isinstance(action, AttachEnergyAction):
            reduce_attach_energy_action(action, state)


def install(namespace):
    from packages.rules.special_energy import install as install_energy
    install_energy(namespace, SPECIAL_ENERGY_SPECS)
    from packages.rules.trainers import install as install_trainers

    install_trainers(namespace, TRAINER_SPECS)
    # The upstream registry only discovers classes owned by the scanned module.
    for spec in SPECS:
        name = "Plain" + spec["effectKey"].split("-", 1)[1]
        namespace[name] = type(
            name, (PlainPokemon,), {"spec": spec, "__module__": namespace["__name__"]}
        )
    for number in ENERGIES:
        name = "Basic" + number
        namespace[name] = type(
            name,
            (BasicEnergy,),
            {"number": number, "__module__": namespace["__name__"]},
        )

from packages.rules.healing import value as healed

from packages.rules.energy_units import matches as energy_matches
