"""Directed physical-card fixtures, separate from legal-deck combination runs."""

import copy
import pytest

from packages.battle.runtime import Adapter, PlayerId, view
from packages.battle.agent import predict
from packages.simulation.fork import RuleFork, Continuation, resume
from packages.rules.invariants import check_conservation, physical_cards
from packages.rules.plain import SPECS, TRAINER_SPECS
from packages.rules.zone_effects import KINDS as ZONE_KINDS
from packages.rules.field_effects import KINDS as FIELD_KINDS

NEW_KINDS = FIELD_KINDS | {
    "staged_attack",
    "coin_branch",
    "attack_extra_prize",
    "discard_damage",
    "copy_attack",
    "target_damage",
    "named_attack_lock",
    "discard_tools_before_damage",
    "opponent_attack_lock",
    "drain_damage",
    "damage_expression",
    "attack_lock",
    "special_status",
    "recoil",
    "ignore_damage_modifiers",
    "sequence",
    "attack_operation",
    "advanced_attack",
}
from packages.simulation.registry import EFFECTS
from ptcg.core.enums import CardPosition as Z, PokemonPosition as P
from ptcg.core.card_registry import registry
from ptcg.core.card import PokemonCard


def scene(source_id, action_type, mode="normal", chance_seed=None):
    # Loading the frozen card list applies the same reviewed corrections as a match.
    lines = [
        f"1 {e['name']} {k.split('-')[0]} {k.split('-')[1]}"
        for k, e in EFFECTS.items()
        if k != "P01-003" and not k.startswith(("P4P-", "P4E-", "P4T-", "P4S-"))
    ]
    lines += [f"{60-len(lines)} Metal Energy SVE 008"]
    game = Adapter(33, lines, lines)
    env, state = game.env, game.env.gamestate
    if chance_seed is not None:
        env.rng.seed(chance_seed)
    env.phase, env.start_stage = "playing", False
    state.turn = state.starting_player = PlayerId.PLAYER1
    p, o = state.player1, state.player2
    factories = {c.id: type(c) for c in p.deck}

    def card(key):
        return factories.get(key, registry.get(key))()

    def put(player, zone, cards):
        setattr(player, zone, cards)
        for i, c in enumerate(cards):
            c.index, c.cardPosition = i + 1, Z[zone.upper()]
            if zone in ("active", "bench"):
                c.position, c.firstTurnPlayed = P[zone.upper()], False
        return cards

    for player in (p, o):
        for zone in ("hand", "left", "prize", "active", "bench", "discard", "lostZone"):
            setattr(player, zone, [])
        player.firstTurn = player.supporterPlayedTurn = player.energyPlayedTurn = False
        player.retreated = False
        put(player, "active", [card("P01-005")])
        put(player, "bench", [card("P01-006"), card("TWM-128")])
        put(player, "prize", [card("SVE-008") for _ in range(6)])
        put(player, "left", [card(k) for k in factories])
        put(player, "discard", [card("SVE-008"), card("SVE-002"), card("P01-005")])
        put(player, "hand", [card("SVE-008") for _ in range(4)])
        player.hasPokemonDead = True
    source = card(source_id)
    if source_id.startswith("P4S-") and source.spec["mechanic"].get("rocket"):
        p.active[0].name = "Team Rocket's Fixture"
    if source_id.startswith("P4T-"):
        rule = source.spec["mechanic"]
        if rule.get('secondFirstTurn'):
            p.firstTurn=True
            state.starting_player=o.id
        if rule.get("afterRocketKnockout"):
            p.knockout_history = [{"turn": state.turn_number - 1, "opponentTurn": True, "name": "Team Rocket's Fixture"}]
        if rule.get("requiresSupporter"):
            p.supporterPlayedTurn = True
        if rule.get("requiresTrait"):
            from ptcg.core.enums import PokemonRule
            p.active[0].pokemonRule = PokemonRule[rule["requiresTrait"]]
        if rule["kind"] == "trainer_operation":
            p.active[0].hp -= 10
            p.active[0].name = "Team Rocket's Fixture"
            p.bench[0].name = "Team Rocket's Fixture"
            o.active[0].attachment = [card("P01-004")]
            if rule["op"] == "briar":
                put(o,"prize",o.prize[:2])
            if rule["op"] in ("pokemon_exchange", "hp_guess"):
                put(p, "hand", p.hand + [card("P01-005")])
            if rule["op"] == "ogre_mask":
                from packages.rules.final_trainers import ogerpon
                candidates = [card(s["effectKey"]) for s in SPECS if "Ogerpon" in s["name"]]
                targets = [c for c in candidates if ogerpon(c)]
                put(p, "bench", p.bench + [targets[0]])
                put(p, "discard", p.discard + [targets[1]])
            if rule["op"] == "rare_candy":
                evolved_spec = next(s for s in SPECS if s["stage"] == "STAGE_2" and any(prev["name"] in s["evolvesFrom"] and prev["stage"] == "STAGE_1" for prev in SPECS))
                evolved = card(evolved_spec["effectKey"])
                p.active[0].name = next(prev["evolvesFrom"][0] for prev in SPECS if prev["name"] in evolved_spec["evolvesFrom"] and prev["stage"] == "STAGE_1")
                put(p, "hand", p.hand + [evolved])
        if rule.get("requiresMorePrizes"):
            put(o, "prize", o.prize[:-1])
        if rule.get("requiresOpponentPoison"):
            o.active[0].poisoned = True
        if source.spec["trainerType"] == "stadium":
            put(p, "hand", p.hand + [card("SVE-005")])
            put(p, "discard", p.discard + [card("SVE-004"), card("SVE-004")])
            p.active[0].hp -= 40
            p.rocket_supporter_turn = state.turn_number
            from ptcg.core.enums import PokemonType, Stage
            basic = card("P01-005")
            basic.name = "Marnie's Fixture"
            basic.stage, basic.pokemonType = Stage.BASIC, PokemonType.NORMAL
            put(p, "left", [basic, card("TEF-159")] + p.left)
        if rule["kind"] == "attach_multiple":
            from ptcg.core.enums import CardType, PokemonRule
            for target in p.active + p.bench:
                if rule.get("targetType"):
                    target.cardType = CardType[rule["targetType"]]
                if rule.get("targetPrefix"):
                    target.name = rule["targetPrefix"] + target.name
                if rule.get("targetTrait"):
                    target.pokemonRule = PokemonRule[rule["targetTrait"]]
            put(p, "left", [card("SVE-004"), card("SVE-004")] + p.left)
            put(p, "discard", p.discard + [card("SVE-002"), card("SVE-008")])
        if rule["kind"] == "transfer_energy":
            p.active[0].attachment = [card("SVE-008")]
            p.active[0].energy = list(p.active[0].attachment[0].provides)
        if rule["kind"] == "discard_field_energy":
            o.active[0].attachment = [card("P01-004"), card("SVE-008")]
            o.active[0].energy = [e for c in o.active[0].attachment for e in c.provides]
        if rule["kind"] in ("heal", "heal_all", "heal_own_all"):
            p.active[0].hp -= 10
            if rule.get("type"):
                from ptcg.core.enums import CardType

                p.active[0].cardType = CardType[rule["type"]]
            if rule.get("maxRemainingHP"):
                p.active[0].hp = min(p.active[0].hp, rule["maxRemainingHP"])
        if rule.get("countTerm") == "ancient":
            from ptcg.core.enums import PokemonRule

            p.active[0].pokemonRule = PokemonRule.ANCIENT
        if rule["kind"] == "discard_hand_to":
            put(o, "hand", o.hand + [card("SVE-008") for _ in range(rule["count"] + 1)])
        if rule["kind"].startswith("recover_") and rule.get("filter", "").startswith(
            "pokemon_energy:"
        ):
            put(p, "discard", p.discard + [card("SVE-005")])
        if source.spec["mechanic"].get("filter") == "supporter":
            put(p, "discard", p.discard + [card("PAF-087")])
        if source.spec["mechanic"].get("lastHand"):
            put(p, "hand", [])
        if source.spec["mechanic"].get("filter") == "name:Nemona":
            spec = next(s for s in TRAINER_SPECS if s["name"] == "Nemona")
            put(p, "discard", [card(spec["effectKey"])])
    if source_id.startswith("P4P-") and action_type == "UseAbilityAction":
        put(p, "hand", p.hand + [card("P4E-001"), card("SVE-005")])
        energies = {
            "GRASS": "P4E-001",
            "FIRE": "SVE-002",
            "WATER": "P4E-003",
            "LIGHTNING": "P4E-004",
            "PSYCHIC": "SVE-005",
            "FIGHTING": "P4E-006",
            "DARK": "P4E-007",
            "METAL": "SVE-008",
        }
        for ability in source.spec.get("abilities", []):
            if ability.get("firstTurnOnly"):
                p.firstTurn = True
            if ability.get("maxOpponentPrizes"):
                put(o, "prize", o.prize[:ability["maxOpponentPrizes"]])
            if ability.get("discardBasicType"):
                put(p, "hand", p.hand + [card(energies[ability["discardBasicType"]])])
            if ability["kind"] == "activated_effect" and ability["effect"]["kind"] == "attach":
                effect = ability["effect"]
                origin = "left" if effect["origin"] == "top" else effect["origin"]
                from packages.rules.plain import SPECIAL_ENERGY_SPECS
                energy_key = next(s["effectKey"] for s in SPECIAL_ENERGY_SPECS if s["name"] == effect["name"]) if effect.get("name") else energies[effect.get("type", "METAL")]
                put(p, origin, [card(energy_key)] + list(getattr(p, origin)))
                if effect.get("targetPrefix"):
                    p.bench[0].name = effect["targetPrefix"] + p.bench[0].name
            if ability["kind"] == "activated_effect" and ability["effect"]["kind"] == "switch_poison":
                from ptcg.core.enums import CardType
                p.bench[0].cardType = CardType.DARK
            if ability["kind"] == "attach_energy":
                origin = ability["origin"]
                put(
                    p,
                    origin,
                    list(getattr(p, origin)) + [card(energies[ability["type"]])],
                )
        if any(a["kind"] == "draw_until" for a in source.spec.get("abilities", [])):
            put(p, "hand", [])
    if action_type in ("AttackAction", "UseAbilityAction") and isinstance(
        source, PokemonCard
    ):
        put(p, "active", [source])
        source.hp -= min(30, source.hp - 10)
        source.attachment = [card("SVE-002"), card("SVE-005"), card("P01-004")] + [
            card("SVE-008") for _ in range(5)
        ]
        if source_id.startswith("P4P-"):
            source.attachment = [
                card(k)
                for k in (
                    "SVE-002",
                    "SVE-005",
                    "SVE-008",
                    "P4E-001",
                    "P4E-003",
                    "P4E-004",
                    "P4E-006",
                    "P4E-007",
                )
                for _ in range(
                    max(
                        1,
                        max(
                            sum(t == card(k).cardType.name for t in a["cost"])
                            for a in source.spec["attacks"]
                        ),
                    )
                )
            ]
    elif action_type == "DiscardDoll":
        from packages.rules.setup_doll import enter
        enter(source)
        put(p, "active", [source])
    elif action_type == "UseStadiumAction":
        state.stadium = [source]
        source.playedFrom = p.id
    else:
        put(p, "hand", [source] + p.hand)
    if source_id.startswith("P4P-") and action_type == "UseAbilityAction" and any(a.get("benchOnly") for a in source.spec.get("abilities", [])):
        put(p, "active", [card("P01-005")])
        put(p, "bench", p.bench + [source])
    if source_id.startswith("P4P-") and action_type == "UseAbilityAction":
        for rule in source.spec.get("abilities", []):
            if rule["kind"] == "hand_bench":
                from ptcg.core.enums import Stage
                source.attachment, source.energy = [], []
                put(p, "active", [card("P01-005")])
                put(p, "hand", p.hand + [source])
                if rule.get("behind"):
                    put(o, "prize", o.prize[:1])
                if rule.get("opponentStage2"):
                    o.active[0].stage = Stage.STAGE_2
            if rule.get("effect", {}).get("kind") == "bottom_leap":
                put(p, "active", [card("P01-005")])
                put(p, "bench", p.bench + [source])
    if source_id.startswith("P4P-") and action_type == "UseAbilityAction" and any(a.get("effect", {}).get("kind") == "heal" and a["effect"].get("stage") == "evolved" for a in source.spec.get("abilities", [])):
        from packages.rules.engine import Gholdengo
        target = Gholdengo()
        target.hp -= 40
        put(p, "active", [target])
        put(p, "bench", p.bench + [source])
    if mode in ("entry", "entry_auto"):
        from ptcg.core.enums import Stage, PokemonRule
        for ability in source.spec.get("abilities", []):
            if ability.get("requiresTrait"):
                p.bench[0].pokemonRule = PokemonRule[ability["requiresTrait"]]
        if action_type == "EvolvePokemonAction":
            p.active[0].name = source.evolveFrom[0]
            p.active[0].stage = Stage.STAGE_1 if source.stage == Stage.STAGE_2 else Stage.BASIC
        p.active[0].hp -= 20
        for origin in ("hand", "left", "discard"):
            put(p, origin, list(getattr(p, origin)) + [card(k) for k in ("SVE-002", "SVE-008", "P4E-006", "P4E-007")])
        put(p, "discard", p.discard + [card("PAF-087")])
        o.active[0].attachment = [card("P01-004")]
    if source_id == "P01-001":
        put(p, "left", [card("OBF-186")] + p.left)
    if source_id.startswith("P4P-") and action_type == "UseAbilityAction":
        for rule in source.spec.get("abilities", []):
            if rule.get("requiresSupporter"):
                p.current_turn_actions.append({"action_type":"UseSupporterAction","source":rule["requiresSupporter"]})
            if rule.get("maxHP"):
                source.hp = rule["maxHP"]
            if rule.get("requiresActiveAbility"):
                from ptcg.core.ability import PassiveAbility
                from ptcg.core.enums import AbilityType
                p.active[0].ability = [PassiveAbility({"name":rule["requiresActiveAbility"],"text":"fixture","abilityType":AbilityType.PASSIVE_ABILITY})]
            if rule.get("effect",{}).get("kind") == "move_basic":
                p.bench[0].attachment = [card("SVE-002")]
            if rule.get("requiresNames"):
                additions = [card("P01-005") for _ in rule["requiresNames"]]
                for addition, name in zip(additions, rule["requiresNames"]):
                    addition.name = name
                put(p, "bench", p.bench + additions)
            if rule.get("requiresTool"):
                spec = next(s for s in TRAINER_SPECS if s["name"] == rule["requiresTool"])
                source.attachment.append(card(spec["effectKey"]))
            if isinstance(rule.get("cost"), dict) and rule["cost"].get("name"):
                # A named discard cost tests physical identity independently of
                # whether that separate Item has a released implementation yet.
                payment = card("TEF-144")
                payment.name = rule["cost"]["name"]
                put(p, "hand", p.hand + [payment])
            if rule.get("effect", {}).get("kind") == "move_energy":
                p.bench[0].attachment = [card("SVE-002")]
            if rule.get("effect", {}).get("kind") == "move_counter":
                p.bench[0].hp -= 20
                if rule["effect"].get("prefix"):
                    p.bench[0].name = rule["effect"]["prefix"] + p.bench[0].name
            if rule["kind"] == "recover_active_status":
                p.active[0].poisoned = True
            if rule.get("stadiumRequired"):
                state.stadium = [card("PAL-171")]
                state.stadium[0].playedFrom = p.id
    if source_id == "PAR-160":
        put(o, "prize", o.prize[:5])
    if source_id.startswith("P4P-"):
        for rule in source.spec.get("abilities", []):
            if rule.get("attackRequires"):
                required = rule["attackRequires"]
                allies = [card("P01-005") for _ in range(required["count"]-1)]
                for ally in allies:
                    ally.name = required["prefix"] + ally.name
                put(p, "bench", allies)
    if source_id.startswith("P4P-") and any(r.get("requiresOpponentEXV") for r in source.spec.get("abilities", [])):
        from ptcg.core.enums import PokemonType
        o.bench[0].pokemonType = PokemonType.EX
    if source_id == "PAR-178" and mode == "tool_attack":
        holder = p.active[0]
        holder.attachment = [source, card("SVE-008")]
        p.hand.remove(source)
    if source_id.startswith("P4T-") and mode == "tool_attack":
        from ptcg.core.enums import PokemonRule
        holder = p.active[0]
        holder.attachment = [source] + [card("SVE-008") for _ in range(3)]
        holder.attachment += [card("P4E-001"), card("SVE-005"), card("P4E-003")]
        holder.pokemonRule = PokemonRule.TERA
        holder.hp -= 20
        o.active[0].hp -= 20
        put(o, "prize", o.prize[:1])
        put(p, "hand", [c for c in p.hand if c is not source])
    if source_id == "MEW-151" and action_type == "AttackAction":
        put(o, "active", [card("TWM-200")])
    if mode == "knockout":
        o.active[0].hp = 10
        o.active[0].attachment = [card("SVE-008")]
        o.bench[0].attachment = [card("P01-002")]
    if mode in ("energy_discard", "discard_knockout"):
        env.rng.seed(1)
        o.active[0].attachment = [card("SVE-008"), card("P01-004")]
        if mode == "discard_knockout":
            o.active[0].hp = 1
            o.active[0].weakness, o.active[0].resistance = [], []
            o.bench[0].attachment = [card("P01-002")]
    if mode == "confusion":
        from ptcg.core.enums import SpecialCondition

        source.special_condition = SpecialCondition.CONFUSED
    if mode in ("retreat_lock", "damage_shield"):
        o.active[0].hp = 1000
    if mode == "self_switch" and source_id.startswith("P4P-"):
        from ptcg.core.enums import CardType
        for attack in source.spec["attacks"]:
            rule = attack.get("mechanic", {})
            if rule.get("kind") == "self_switch" and rule.get("type"):
                p.bench[0].cardType = CardType[rule["type"]]
    if mode in ("opponent_switch", "discard_typed_energy"):
        o.active[0].hp = 1000
        o.active[0].attachment = [card("P4E-003"), card("SVE-008")]
    # Deck ownership is a physical multiset; these are constructed rule states.
    if mode in ZONE_KINDS | NEW_KINDS:
        o.active[0].hp = 1000
        o.active[0].attachment = [card("P4E-003"), card("SVE-008")]
        if mode == "search_attach":
            put(
                p,
                "left",
                p.left
                + [
                    card(k)
                    for k in ("P4E-001", "P4E-003", "P4E-004", "P4E-006", "P4E-007")
                ],
            )
        if mode == "search_hand":
            for a in source.spec["attacks"]:
                r = a.get("mechanic",{})
                if r.get("kind") == "search_hand" and r.get("filter", "").startswith("basic_energy:"):
                    from packages.rules.plain import ENERGIES
                    required = r["filter"].split(":", 1)[1]
                    candidates = [card("P4E-" + n) for n, (_, energy_type) in ENERGIES.items() if energy_type == required]
                    assert candidates, required
                    put(p, "left", candidates + p.left)
                if r.get("kind") == "search_hand" and r.get("filter","").startswith(("pokemon:","prefix:")):
                    from ptcg.core.enums import CardType
                    candidate = card("P01-005")
                    if r["filter"].startswith("prefix:"): candidate.name = r["filter"][7:]+candidate.name
                    else: candidate.cardType = CardType[r["filter"][8:]]
                    put(p,"left",[candidate]+p.left)
        if mode == "search_bench":
            from ptcg.core.enums import CardType, Stage
            for attack in source.spec["attacks"]:
                rule = attack.get("mechanic", {})
                if rule.get("kind") == "search_bench":
                    candidate = card("P01-005")
                    candidate.stage = Stage.BASIC
                    if rule.get("names"):
                        candidate.name = rule["names"][0]
                    if rule.get("prefix"):
                        candidate.name = rule["prefix"] + candidate.name
                    if rule.get("type"):
                        candidate.cardType = CardType[rule["type"]]
                    put(p, "left", p.left + [candidate])
    for player in (p, o):
        from packages.rules.core_fixes import refresh_energy

        for pokemon in player.active + player.bench:
            pokemon.dynamic_energy = True
            refresh_energy(pokemon)
        owned = []

        def collect(c):
            owned.append(c)
            for child in getattr(c, "attachment", []) + getattr(c, "evolved", []):
                collect(child)

        for zone in ("hand", "left", "prize", "active", "bench", "discard", "lostZone"):
            for c in getattr(player, zone):
                collect(c)
        if player is p:
            owned += state.stadium
        player.deck = owned
    game.obs, _, game.done, game.info = env._prepare_step_result()
    env.reducer = resume(env, (game.obs, 0, game.done, game.info))
    next(env.reducer)
    branch = RuleFork(game)
    actions = [
        a
        for a in game.actions
        if type(a).__name__ == action_type
        and (a.source is source or getattr(a, "effect_source", None) is source)
    ]
    assert actions, (source_id, action_type, mode)
    action = actions[-1] if mode == "knockout" or source_id == "TWM-200" else actions[0]
    if mode in ("energy_discard", "discard_knockout"):
        action = next(
            a
            for a in actions
            if source.spec["attacks"][source.attacks.index(a.attack_template)]
            .get("mechanic", {})
            .get("kind")
            == "coin_discard_energy"
        )
    if mode == "retreat_lock":
        action = next(
            a
            for a in actions
            if source.spec["attacks"][source.attacks.index(a.attack_template)]
            .get("mechanic", {})
            .get("kind")
            == "prevent_retreat"
        )
    if (
        mode
        in {"opponent_switch", "discard_typed_energy", "damage_shield"}
        | ZONE_KINDS
        | NEW_KINDS
    ):
        action = next(
            a
            for a in actions
            if source.spec["attacks"][source.attacks.index(a.attack_template)]
            .get("mechanic", {})
            .get("kind")
            == mode
        )
    command = {
        "commandId": "directed-start",
        "expectedStateVersion": 0,
        "decisionId": "d0",
        "choice": {"optionId": f"d0:o{list(game.actions).index(action)}"},
    }
    mandatory_mill=list(p.discard)+list(p.left[:5]) if mode=='entry_auto' else None
    branch.submit(game.actor, command)
    if mandatory_mill is not None:
        assert p.discard==mandatory_mill
    return branch


CASES = (
    [
        (key, kind, "normal")
        for key, kind in [
            ("OBF-186", "UseSupporterAction"),
            ("PAF-084", "UseItemAction"),
            ("PAF-091", "UseItemAction"),
            ("PAL-189", "UseItemAction"),
            ("PAL-265", "UseSupporterAction"),
            ("PAR-160", "UseItemAction"),
            ("PAR-163", "UseItemAction"),
            ("SFA-061", "UseItemAction"),
            ("TEF-144", "UseItemAction"),
            ("TEF-145", "UseSupporterAction"),
            ("TEF-157", "UseItemAction"),
            ("PAL-171", "UseStadiumAction"),
            ("P01-001", "UseAbilityAction"),
            ("TEF-129", "UseAbilityAction"),
            ("TWM-129", "UseAbilityAction"),
            ("TWM-095", "UseAbilityAction"),
            ("MEW-151", "AttackAction"),
            ("PAR-139", "AttackAction"),
            ("TWM-200", "AttackAction"),
            ("SFA-092", "AttackAction"),
            ("P01-005", "AttackAction"),
            ("P01-006", "AttackAction"),
        ]
    ]
    + [
        ("PAR-178", "AttackAction", "tool_attack"),
        ("PAR-139", "AttackAction", "knockout"),
        ("P01-005", "AttackAction", "confusion"),
    ]
    + [
        (key, kind, "atomic")
        for key, kind in [
            ("PAF-080", "UseSupporterAction"),
            ("PAF-087", "UseSupporterAction"),
            ("P01-002", "UseToolAction"),
            ("TEF-159", "UseToolAction"),
            ("P01-003", "AttachEnergyAction"),
            ("P01-004", "AttachEnergyAction"),
            ("SVE-002", "AttachEnergyAction"),
            ("SVE-005", "AttachEnergyAction"),
            ("SVE-008", "AttachEnergyAction"),
            ("TWM-128", "AttackAction"),
        ]
    ]
)


CASES += [
    (key, "AttackAction" if key.startswith("P4P-") else "AttachEnergyAction", "atomic")
    for key in EFFECTS
    if key.startswith(("P4P-", "P4E-", "P4S-"))
]

CASES += [
    (
        s["effectKey"],
        "DiscardDoll" if s["mechanic"]["kind"] == "setup_doll" else {
            "supporter": "UseSupporterAction",
            "item": "UseItemAction",
            "tool": "UseToolAction",
            "stadium": "PutStadiumAction",
        }[s["trainerType"]],
        "atomic",
    )
    for s in TRAINER_SPECS
]
CASES += [(s["effectKey"], "UseStadiumAction", "atomic") for s in TRAINER_SPECS if s["trainerType"] == "stadium" and s["mechanic"].get("use")]
CASES += [(s["effectKey"], "AttackAction", "tool_attack") for s in TRAINER_SPECS if s["mechanic"].get("grantedAttack")]
CASES += [
    (s["effectKey"], "PlayPokemonAction" if a["trigger"] == "bench" else "EvolvePokemonAction", "entry_auto" if a.get('mandatory') else "entry")
    for s in SPECS
    for a in s.get("abilities", [])
    if a["kind"] == "on_entry"
]

CASES += [
    (s["effectKey"], "UseAbilityAction", "atomic")
    for s in SPECS
    if any(a["trigger"] == "activated" for a in s.get("abilities", []))
]

CASES += [
    (s["effectKey"], "AttackAction", mode)
    for s in SPECS
    if any(
        a.get("mechanic", {}).get("kind") == "coin_discard_energy" for a in s["attacks"]
    )
    for mode in ("energy_discard", "discard_knockout")
    if mode != "discard_knockout"
    or any(
        a.get("mechanic", {}).get("kind") == "coin_discard_energy" and a["damage"] > 0
        for a in s["attacks"]
    )
]
CASES += [
    (s["effectKey"], "AttackAction", "retreat_lock")
    for s in SPECS
    if any(a.get("mechanic", {}).get("kind") == "prevent_retreat" for a in s["attacks"])
]


CASES += [
    (s["effectKey"], "AttackAction", a["mechanic"]["kind"])
    for s in SPECS
    for a in s["attacks"]
    if a.get("mechanic", {}).get("kind")
    in ("opponent_switch", "discard_typed_energy", "damage_shield")
]


CASES += [
    (s["effectKey"], "AttackAction", a["mechanic"]["kind"])
    for s in SPECS
    for a in s["attacks"]
    if a.get("mechanic", {}).get("kind") in ZONE_KINDS | NEW_KINDS
]


@pytest.mark.parametrize("source,kind,mode", CASES)
def test_directed_pause_continuations(source, kind, mode):
    branch = scene(source, kind, mode)
    initial_twin = RuleFork.restore(
        Continuation.from_json(branch.checkpoint().to_json())
    )
    assert initial_twin.game.private_digest() == branch.game.private_digest()
    for viewer in PlayerId:
        assert view(initial_twin.game, viewer) == view(branch.game, viewer)
    if mode == "retreat_lock":
        assert view(branch.game, PlayerId.PLAYER2)["observation"]["self"]["active"][0][
            "retreatBlocked"
        ]
    if mode == "damage_shield":
        shield=branch.game.env.gamestate.player1.active[0].damage_shield
        visible=view(branch.game,PlayerId.PLAYER1)['observation']['self']['active'][0]
        assert visible['attackDamageReduction']==shield['amount']
        if 'maximum' in shield:
            assert visible['preventAttackDamageAtMost']==shield['maximum']
        else:
            assert visible['attackDamageReduction']>0
    pauses = 0
    out_of_turn = False
    hidden = False
    for _ in range(30):
        game = branch.game
        check_conservation(game.env.gamestate)
        if game.done or branch.regular(game):
            break
        pauses += 1
        out_of_turn |= game.actor != game.env.gamestate.turn
        token = branch.checkpoint().to_json()
        restored = RuleFork.restore(Continuation.from_json(token))
        for viewer in PlayerId:
            assert view(restored.game, viewer) == view(game, viewer)
        before = game.private_digest()
        command = predict(view(game, game.actor))[0]
        # Force nonempty, maximal selections to reach nested effect branches.
        d = view(game, game.actor)["decision"]
        hidden |= d.get("hidden", False)
        if mode == "entry" and d.get("options"):
            command["choice"] = {"optionId": d["options"][-1]["id"]}
        if d["kind"] == "selection":
            command["choice"] = {
                "selectedRefs": [c["ref"] for c in d["candidates"][: d["max"]]]
            }
            if source == "MEW-151" and pauses == 1:
                command["choice"] = {"selectedRefs": [d["candidates"][-1]["ref"]]}
        restored.submit(restored.game.actor, copy.deepcopy(command))
        assert game.private_digest() == before
        branch.submit(game.actor, command)
        assert restored.game.private_digest() == game.private_digest()
        assert len(physical_cards(restored.game.env.gamestate)) == len(
            physical_cards(game.env.gamestate)
        )
    else:
        pytest.fail("Directed effect did not return to an ordinary boundary")
    if mode == 'entry_auto':
        assert pauses == 0
    if mode not in (
            {"confusion", "atomic", "retreat_lock", "damage_shield", "tool_attack", "entry_auto"}
        | ZONE_KINDS
        | NEW_KINDS
    ):
        assert pauses, (source, kind, mode)
    if mode in {
        "search_hand",
        "search_bench",
        "self_switch",
        "discard_opponent_energy",
    }:
        assert pauses, (source, mode)
    if mode == "search_attach":
        spec = next(s for s in SPECS if s["effectKey"] == source)
        mechanic = next(
            a["mechanic"]
            for a in spec["attacks"]
            if a.get("mechanic", {}).get("kind") == mode
        )
        assert pauses >= (2 if mechanic["target"] == "any" else 1), (source, mode)
    if source in ("TEF-145", "TWM-095", "MEW-151", "PAR-178"):
        assert pauses >= 2, "Nested choices must actually execute"
    if mode in ("knockout", "discard_knockout"):
        assert out_of_turn and hidden, (
            "Must exercise defender choice and hidden prize choice"
        )


def test_all_setup_pause_phases_have_serializable_continuations():
    required = {
        "choose_order",
        "mulligan",
        "active",
        "bench",
        "mulligan_bonus",
        "bonus_bench",
    }
    seen = set()
    for seed in range(100):
        branch = RuleFork(Adapter(seed))
        for _ in range(40):
            game = branch.game
            if game.env.phase == "playing":
                break
            seen.add(game.env.phase)
            dto = view(game, game.actor)
            command = predict(dto)[0]
            if game.env.phase == "mulligan_bonus":
                command["choice"] = {"optionId": dto["decision"]["options"][-1]["id"]}
            twin = RuleFork.restore(
                Continuation.from_json(branch.checkpoint().to_json())
            )
            before = game.private_digest()
            twin.submit(twin.game.actor, command)
            assert game.private_digest() == before
            branch.submit(game.actor, command)
            assert twin.game.private_digest() == game.private_digest()
        if seen >= required:
            break
    assert seen >= required


def test_frozen_effect_denominator_has_a_directed_case_for_every_effect():
    tested = {case[0] for case in CASES}
    assert set(EFFECTS) <= tested
    # Historical regression cases remain useful after a printing is rebound.
    assert all(registry.get(key) is not None for key in tested)


def test_both_coin_outcomes_replay_and_do_not_share_random_streams():
    outcomes = set()
    for seed in range(8):
        branch = scene("P01-005", "AttackAction", "confusion", chance_seed=seed)
        twin = RuleFork.restore(Continuation.from_json(branch.checkpoint().to_json()))
        assert twin.game.private_digest() == branch.game.private_digest()
        outcomes.add(branch.game.env.gamestate.player1.active[0].hp)
        before = branch.game.env.rng.getstate()
        twin.game.env.rng.random()
        assert branch.game.env.rng.getstate() == before
    assert outcomes == {10, 40}
