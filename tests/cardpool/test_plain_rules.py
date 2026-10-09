import pytest
from scripts.cardpool.compile_plain import compile_rule
from packages.rules.plain import SPECS, ENERGIES
from ptcg.core.card_registry import registry
from ptcg.core.action import AttackAction, EvolvePokemonAction
from ptcg.core.enums import CardType as T, PokemonPosition as P
from ptcg.core.reducer import _calculate_damage, reduce_attack_damage
from test_effects import context, zone, drive


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s["effectKey"])
def test_each_plain_rule_cost_damage_bench_and_type_modifiers(spec):
    state, player, opponent = context()
    source = registry.get(spec["effectKey"])()
    target = registry.get("P01-005")()
    zone(player, "active", [source])
    zone(opponent, "active", [target])
    # This matrix isolates printed cost/damage; aura interactions have directed tests.
    source.spec = {**source.spec, "abilities": []}
    source.ability = []
    for attack, definition in zip(source.attacks, spec["attacks"]):
        contract = definition.get("mechanic", {})
        if contract.get("requiresLastAttack"):
            source.attack_history = [{"turn": state.turn_number - 2, "name": contract["requiresLastAttack"]}]
        if contract.get("onlySecondFirst"):
            player.firstTurn = True
            state.starting_player = opponent.id
        source.energy = attack.cost[:]
        assert any(
            isinstance(a, AttackAction) and a.attack_template is attack
            for a in source.get_actions(state)
        )
        if attack.cost:
            source.energy.pop()
            assert not any(
                isinstance(a, AttackAction) and a.attack_template is attack
                for a in source.get_actions(state)
            )
        source.energy = attack.cost[:]
        source.position = P.BENCH
        bench_actions=[a for a in source.get_actions(state) if isinstance(a,AttackAction)]
        assert all(a.attack_template.compiled_rule.get('mechanic',{}).get('benchAttack') for a in bench_actions)
        assert any(a.attack_template is attack for a in bench_actions)==bool(contract.get('benchAttack'))
        source.position = P.ACTIVE
        target.hp = 1000
        target.weakness, target.resistance = [], []
        drive(
            reduce_attack_damage(AttackAction(player.id, source, attack, target), state)
        )
        assert target.hp == 1000 - definition["damage"]
        target.weakness = [source.cardType]
        assert (
            _calculate_damage(source, target, definition["damage"], state)
            == 2 * definition["damage"]
        )
        target.weakness, target.resistance = [], [source.cardType]
        assert _calculate_damage(source, target, definition["damage"], state) == max(
            0, definition["damage"] - 30
        )
    other = type(source)()
    assert other.attachment == [] and other.energy == [] and other.hp == spec["hp"]


@pytest.mark.parametrize("number", ENERGIES)
def test_additional_basic_energy_attaches_once_and_pays_only_its_type(number):
    state, player, opponent = context()
    pokemon = registry.get(SPECS[0]["effectKey"])()
    card = registry.get("P4E-" + number)()
    zone(player, "active", [pokemon])
    zone(player, "hand", [card])
    player.energyPlayedTurn = False
    options = card.get_actions(state)
    assert len(options) == 1
    card.reduce_action(options[0], state)
    assert card in pokemon.attachment and card not in player.hand
    assert pokemon.energy == card.provides
    assert player.energyPlayedTurn and not card.get_actions(state)
    from packages.rules.core_fixes import check_energy

    assert check_energy([card.cardType], pokemon.energy)
    assert not check_energy([T.FIRE], pokemon.energy)


@pytest.mark.parametrize(
    "spec", [s for s in SPECS if s["stage"] != "BASIC"], ids=lambda s: s["effectKey"]
)
def test_plain_evolution_transfers_damage_and_attachments_and_blocks_same_turn(spec):
    state, player, opponent = context()
    evolved = registry.get(spec["effectKey"])()
    # Isolate evolution transfer. Entry healing and passive HP are tested in
    # test_entry_effects/test_maximum_hp with their actual effects enabled.
    evolved.spec = {**evolved.spec, "abilities": []}
    evolved.ability = []
    # A fixture isolates the predecessor's name and damage; its attacks are not used.
    previous = registry.get(
        next(s["effectKey"] for s in SPECS if s["stage"] == "BASIC")
    )()
    previous.name = spec["evolvesFrom"][0]
    previous.spec = {**previous.spec, "abilities": []}
    previous.ability = []
    previous.hp -= 20
    attached = registry.get("SVE-008")()
    previous.attachment = [attached]
    previous.energy = [T.METAL]
    zone(player, "active", [previous])
    zone(player, "hand", [evolved])
    previous.firstTurnPlayed = True
    assert not any(
        isinstance(a, EvolvePokemonAction) for a in player.get_actions(state)
    )
    previous.firstTurnPlayed = False
    action = next(
        a for a in player.get_actions(state) if isinstance(a, EvolvePokemonAction)
    )
    drive(evolved.reduce_action(action, state))
    assert player.active == [evolved] and evolved not in player.hand
    assert evolved.hp == spec["hp"] - 20
    assert evolved.evolved == [previous] and evolved.attachment == [attached]
    assert (
        evolved.energy == [T.METAL] and not previous.energy and not previous.attachment
    )


PLAIN = """{{N|皮卡丘||ピカチュウ|Pikachu}}
==卡牌信息==
{{卡牌信息/header|name=皮卡丘|hp=60|type=雷|evostage=基础}}
{{卡牌信息/attack|ename=Gnaw|ZHSname=咬住|cost=无|damage=20}}
{{卡牌信息/svend|type=雷|weakness=斗|resistance=|retreat=1}}
{{ExpansionList/header/zh|雷}}"""


@pytest.mark.parametrize(
    "spec",
    [
        s
        for s in SPECS
        if any(
            a.get("mechanic", {}).get("kind")
            in ("coin_fail", "coin_bonus", "coin_discard_energy")
            for a in s["attacks"]
        )
    ],
    ids=lambda s: s["effectKey"],
)
@pytest.mark.parametrize("heads", [True, False])
def test_coin_damage_uses_seeded_rng_ends_turn_and_preserves_definition(spec, heads):
    import random
    from copy import deepcopy

    state, player, opponent = context()
    source = registry.get(spec["effectKey"])()
    target = registry.get(spec["effectKey"])()
    zone(player, "active", [source])
    zone(opponent, "active", [target])
    definition = next(
        a
        for a in spec["attacks"]
        if a.get("mechanic", {}).get("kind")
        in ("coin_fail", "coin_bonus", "coin_discard_energy")
    )
    attack = source.attacks[spec["attacks"].index(definition)]
    source.energy = attack.cost[:]
    target.hp, target.weakness, target.resistance = 1000, [source.cardType], []
    # This matrix checks coin damage; retaliatory abilities have separate tests.
    target.spec = {**target.spec, "abilities": []}
    target.ability = []
    # Random(1) gives heads, Random(0) tails. Use the real state RNG.
    state.rng = random.Random(1 if heads else 0)
    before = deepcopy(attack.__dict__)
    action = AttackAction(player.id, source, attack, target)
    drive(source.reduce_action(action, state))
    mechanic = definition["mechanic"]
    damage = definition["damage"]
    if mechanic["kind"] == "coin_fail" and not heads:
        damage = 0
    elif mechanic["kind"] == "coin_bonus" and heads:
        damage += mechanic["bonus"]
    assert target.hp == 1000 - 2 * damage
    assert state.turn == opponent.id
    assert attack.__dict__ == before
    assert sum("Coin flip:" in event for event in state.auto_events) == 1


def test_coin_compiler_rejects_mismatched_bonus_or_extra_sentence():
    effect = "|damageP=加|eeffect=Flip a coin. If heads, this attack does 30 more damage.|effectZHS=抛掷1次硬币如果为正面，则追加造成30伤害。"
    page = PLAIN.replace("|damage=20", "|damage=20" + effect)
    assert compile_rule({"text": page})["attacks"][0]["mechanic"] == {
        "kind": "coin_bonus",
        "bonus": 30,
    }
    assert compile_rule({"text": page.replace("30 more", "40 more")}) is None
    assert compile_rule({"text": page.replace("30伤害。", "30伤害。抽1张卡。")}) is None


def test_known_turn_typo_requires_both_other_language_rules():
    text = PLAIN.replace(
        "|damage=20",
        "|damage=20|eeffect=During your opponent's next turn, the Defending Pokémon can't retreat.|effectZHS=在下一个对手的固合，受到这个招式影响的宝可梦，无法撤退。|effectZHT=在下個對手的回合，受到這個招式的寶可夢無法撤退。",
    )
    assert compile_rule({"text": text})["attacks"][0]["mechanic"] == {
        "kind": "prevent_retreat"
    }
    assert compile_rule({"text": text.replace("無法撤退。", "受到20傷害。")}) is None
    assert (
        compile_rule({"text": text.replace("can't retreat.", "takes 20 damage.")})
        is None
    )


def test_coin_count_compiler_requires_matching_count_damage_and_multiplier():
    suffix = "|damageP=乘|eeffect=Flip 3 coins. This attack does 20 damage for each heads.|effectZHS=抛掷3次硬币，造成正面次数×20伤害。"
    page = PLAIN.replace("|damage=20", "|damage=20" + suffix)
    assert compile_rule({"text": page})["attacks"][0]["mechanic"] == {
        "kind": "coin_count",
        "count": 3,
        "perHead": 20,
    }
    for changed in (
        page.replace("Flip 3", "Flip 2"),
        page.replace("×20", "×30"),
        page.replace("|damageP=乘", ""),
        page.replace("伤害。", "伤害。抽1张卡。"),
    ):
        assert compile_rule({"text": changed}) is None


def test_bonus_suffix_and_punctuation_are_equivalent_but_extra_text_is_not():
    effect = "|eeffect=Flip a coin. If heads, this attack does 20 more damage.|effectZHS=抛掷1次硬币如果为正面,则追加造成20伤害。"
    page = PLAIN.replace("|damage=20", "|damage=10+" + effect)
    parsed = compile_rule({"text": page})["attacks"][0]
    assert parsed["damage"] == 10 and parsed["mechanic"] == {
        "kind": "coin_bonus",
        "bonus": 20,
    }
    assert (
        compile_rule({"text": page.replace("20伤害。", "20伤害。令对手中毒。")}) is None
    )


@pytest.mark.parametrize("remaining", [0, 1, 3])
def test_collect_draws_only_available_cards_and_never_deals_damage(remaining):
    spec = next(
        s
        for s in SPECS
        if any(
            a.get("mechanic") == {"kind": "draw", "count": 1} and a["damage"] == 0
            for a in s["attacks"]
        )
    )
    state, player, opponent = context()
    source = registry.get(spec["effectKey"])()
    zone(player, "active", [source])
    zone(player, "left", list(player.left[:remaining]))
    before = list(player.left)
    target = opponent.active[0]
    hp = target.hp
    index = next(
        i
        for i, a in enumerate(spec["attacks"])
        if a.get("mechanic") == {"kind": "draw", "count": 1} and a["damage"] == 0
    )
    drive(
        source.reduce_action(
            AttackAction(player.id, source, source.attacks[index], target), state
        )
    )
    assert player.hand == before[:1] and player.left == before[1:]
    assert target.hp == hp and state.turn == opponent.id


def test_closed_grammar_matches_independent_fixture_and_rejects_other_rules():
    spec = compile_rule({"text": PLAIN})
    assert spec["hp"] == 60 and spec["type"] == "LIGHTNING"
    assert spec["weakness"] == ["FIGHTING"] and spec["retreat"] == 1
    assert spec["attacks"] == [
        {"name": "Gnaw", "cnName": "咬住", "damage": 20, "cost": ["COLORLESS"]}
    ]
    for addition in (
        "|effectZHS=令对手中毒",
        "|eeffect=Draw a card.",
        "|damageP=加",
        "|futureMechanic=1",
    ):
        assert (
            compile_rule({"text": PLAIN.replace("|damage=20", "|damage=20" + addition)})
            is None
        )
    assert (
        compile_rule(
            {
                "text": PLAIN.replace(
                    "{{卡牌信息/attack",
                    "{{卡牌信息/power|effectZHS=特性}}\n{{卡牌信息/attack",
                )
            }
        )
        is None
    )
    assert (
        compile_rule({"text": PLAIN.replace("|evostage=基础", "|evostage=1阶进化")})
        is None
    )
    assert compile_rule({"text": PLAIN.replace("|hp=60", "|hp=60|tera=1")}) is None
    assert (
        compile_rule(
            {"text": PLAIN.replace("==卡牌信息==", "==卡牌信息==\n特殊规则说明")}
        )
        is None
    )
