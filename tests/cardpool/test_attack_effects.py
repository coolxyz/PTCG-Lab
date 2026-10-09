"""Attack effects resolve before knockouts, and restrictions expire correctly."""

import random
import pytest
from packages.rules.plain import SPECS
from packages.rules.core_fixes import refresh_energy
from packages.rules.adapter import Adapter
from ptcg.core.card_registry import registry
from ptcg.core.action import AttackAction, RetreatAction, EvolvePokemonAction
from ptcg.utils.utils import next_turn, switch_pokemon
from test_effects import context, zone, drive


def fixture(kind):
    spec = next(
        s
        for s in SPECS
        if any(a.get("mechanic", {}).get("kind") == kind for a in s["attacks"])
    )
    state, player, opponent = context()
    source = registry.get(spec["effectKey"])()
    zone(player, "active", [source])
    target = opponent.active[0]
    target.hp = 500
    target.weakness = target.resistance = []
    index = next(
        i
        for i, a in enumerate(spec["attacks"])
        if a.get("mechanic", {}).get("kind") == kind
    )
    return state, player, opponent, source, target, source.attacks[index]


@pytest.mark.parametrize("clear", ["end_turn", "switch", "evolve"])
def test_retreat_block_is_public_and_clears_on_turn_switch_or_evolution(clear):
    state, player, opponent, source, target, attack = fixture("prevent_retreat")
    target.retreat = []
    zone(opponent, "bench", [registry.get("P01-006")()])
    drive(source.reduce_action(AttackAction(player.id, source, attack, target), state))
    assert state.turn == opponent.id
    assert Adapter._card(target)["retreatBlocked"]
    assert not any(isinstance(a, RetreatAction) for a in opponent.get_actions(state))
    if clear == "end_turn":
        next_turn(state)
        assert not hasattr(target, "retreat_blocked_turn")
        next_turn(state)
    elif clear == "switch":
        bench = opponent.bench[0]
        switch_pokemon(target, bench, opponent)
        switch_pokemon(bench, target, opponent)
    else:
        evolved_spec = next(s for s in SPECS if s["stage"] == "STAGE_1")
        evolved = registry.get(evolved_spec["effectKey"])()
        zone(opponent, "hand", [evolved])
        drive(
            evolved.reduce_action(
                EvolvePokemonAction(opponent.id, evolved, target), state
            )
        )
        target = evolved
        target.retreat = []
    assert "retreatBlocked" not in Adapter._card(target)
    assert any(isinstance(a, RetreatAction) for a in opponent.get_actions(state))


@pytest.mark.parametrize("heads", [True, False])
@pytest.mark.parametrize("attached", [0, 1, 2])
def test_energy_discard_selects_one_physical_card_and_refreshes_special_energy(
    heads, attached
):
    state, player, opponent, source, target, attack = fixture("coin_discard_energy")
    target.attachment = [registry.get("P01-004")(), registry.get("SVE-008")()][
        :attached
    ]
    original = list(target.attachment)
    target.dynamic_energy = True
    refresh_energy(target)
    state.rng = random.Random(1 if heads else 0)
    seen = drive(
        source.reduce_action(AttackAction(player.id, source, attack, target), state),
        lambda actions, info, n: list(actions)[-1],
    )
    assert len(seen) == int(heads and attached > 0)
    assert len(target.attachment) == attached - int(heads and attached > 0)
    if heads and attached:
        assert original[-1] in opponent.discard
    assert target.energy == [e for c in target.attachment for e in c.provides]
    assert state.turn == opponent.id


def test_energy_discard_precedes_prizes_and_exp_share_on_knockout():
    state, player, opponent, source, target, attack = fixture("coin_discard_energy")
    target.hp = 1
    target.attachment = [registry.get("SVE-008")()]
    energy = target.attachment[0]
    target.energy = energy.provides[:]
    holder = registry.get("P01-006")()
    holder.attachment = [registry.get("P01-002")()]
    zone(opponent, "bench", [holder, registry.get("P01-005")()])
    state.rng = random.Random(1)
    gen = source.reduce_action(AttackAction(player.id, source, attack, target), state)
    first = next(gen)
    assert first[3]["raw_available_actions"][0].chosen == [energy]
    assert target.hp <= 0 and target in opponent.active and len(player.prize) == 6
    # Finish the already-started generator, including any Exp Share holder choice.
    item = gen.send(first[3]["raw_available_actions"][0])
    try:
        while True:
            item = gen.send(list(item[3]["raw_available_actions"])[-1])
    except StopIteration:
        pass
    assert energy in opponent.discard and energy not in holder.attachment
    assert target in opponent.discard and len(player.prize) == 5


@pytest.mark.parametrize(
    "kind",
    [
        "prevent_retreat",
        "coin_discard_energy",
        "discard_typed_energy",
        "opponent_switch",
    ],
)
def test_mew_copies_attack_effect_using_its_own_physical_source(kind):
    from packages.rules.effects import Mew

    state, player, opponent, original, _, attack = fixture(kind)
    mew = Mew()
    zone(player, "active", [mew])
    zone(opponent, "active", [original])
    original.hp = 1000
    original.weakness, original.resistance = [mew.cardType], []
    original.attachment = [
        registry.get("P4E-003" if kind == "discard_typed_energy" else "SVE-008")()
    ]
    if kind == "opponent_switch":
        zone(opponent, "bench", [registry.get("P01-006")()])
    original.energy = original.attachment[0].provides[:]
    state.rng = random.Random(1)

    def choose(actions, info, n):
        if n == 1:
            return next(a for a in actions if a.chosen == [attack])
        return list(actions)[0]

    drive(
        mew._genome_hacking_attack(
            AttackAction(player.id, mew, mew.attacks[0], original), state
        ),
        choose,
    )
    assert original.hp == 1000 - 2 * attack.damage
    if kind == "prevent_retreat":
        assert original.retreat_blocked_turn == state.turn_number
    elif kind == "opponent_switch":
        assert original in opponent.bench and original not in opponent.active
    else:
        assert original.attachment == [] and original.energy == []


@pytest.mark.parametrize("knockout", [False, True])
@pytest.mark.parametrize("bench_count", [0, 1, 2])
def test_defender_chooses_switch_before_knockout_without_switching_attacker(
    knockout, bench_count
):
    from ptcg.core.exceptions import GameTermination

    state, player, opponent, source, target, attack = fixture("opponent_switch")
    target.hp = 1 if knockout else 1000
    target.retreat_blocked_turn = state.turn_number + 1
    bench = [registry.get("P01-006")() for _ in range(bench_count)]
    zone(opponent, "bench", bench[:])
    seen = []

    def choose(actions, info, n):
        if n == 1 and bench_count:
            assert actions[0].playerId == opponent.id
            assert all(a.chosen[0] in bench for a in actions)
        seen.append(info)
        return list(actions)[-1]

    try:
        drive(
            source.reduce_action(
                AttackAction(player.id, source, attack, target), state
            ),
            choose,
        )
    except GameTermination:
        assert knockout and bench_count == 0
    assert player.active == [source]
    if bench_count:
        assert opponent.active == [bench[-1]]
        assert not hasattr(target, "retreat_blocked_turn")
    if knockout:
        assert target in opponent.discard and len(player.prize) == 5
    elif bench_count:
        assert target in opponent.bench
    else:
        assert seen == [] and opponent.active == [target]


@pytest.mark.parametrize("second_special", [False, True])
def test_typed_discard_filters_current_provided_type_including_any(second_special):
    state, player, opponent, source, target, attack = fixture("discard_typed_energy")
    water = registry.get("P4E-003")()
    fire = registry.get("SVE-002")()
    luminous = registry.get("P01-004")()
    target.attachment = [water, fire, luminous]
    if second_special:
        target.attachment.append(registry.get("P01-004")())
    target.dynamic_energy = True
    refresh_energy(target)
    rng = state.rng.getstate()

    def choose(actions, info, n):
        candidates = [a.chosen[0] for a in actions]
        assert water in candidates and fire not in candidates
        assert (luminous in candidates) == (not second_special)
        return next(a for a in actions if a.chosen == [water])

    drive(
        source.reduce_action(AttackAction(player.id, source, attack, target), state),
        choose,
    )
    assert water in opponent.discard and fire in target.attachment
    assert state.rng.getstate() == rng


@pytest.mark.parametrize("seed", [0, 1, 5])
@pytest.mark.parametrize("kind", ["coin_count", "coin_until_tails"])
def test_coin_counts_use_exact_rng_sequence_and_preserve_attack(seed, kind):
    state, player, opponent, source, target, attack = fixture(kind)
    definition = source.spec["attacks"][source.attacks.index(attack)]
    mechanic = definition["mechanic"]
    target.hp = 100000
    expected = random.Random(seed)
    if kind == "coin_count":
        heads = sum(expected.randint(0, 1) == 0 for _ in range(mechanic["count"]))
    else:
        heads = 0
        while expected.randint(0, 1) == 0:
            heads += 1
    state.rng = random.Random(seed)
    before = attack.damage
    drive(source.reduce_action(AttackAction(player.id, source, attack, target), state))
    assert target.hp == 100000 - heads * mechanic["perHead"]
    assert state.rng.getstate() == expected.getstate()
    assert attack.damage == before and state.turn == opponent.id


@pytest.mark.parametrize("remaining", [0, 1, 5])
def test_damage_and_draw_happens_before_prizes_even_with_short_deck(remaining):
    spec = next(
        s
        for s in SPECS
        if any(
            a.get("mechanic", {}).get("kind") == "draw"
            and a["damage"] > 0
            and a["mechanic"]["count"] == 2
            for a in s["attacks"]
        )
    )
    state, player, opponent = context()
    source = registry.get(spec["effectKey"])()
    zone(player, "active", [source])
    target = opponent.active[0]
    target.hp, target.weakness, target.resistance = 1, [], []
    zone(opponent, "bench", [registry.get("P01-006")()])
    zone(player, "left", list(player.left[:remaining]))
    drawn = list(player.left[:2])
    index = next(
        i
        for i, a in enumerate(spec["attacks"])
        if a.get("mechanic") == {"kind": "draw", "count": 2} and a["damage"] > 0
    )
    gen = source.reduce_action(
        AttackAction(player.id, source, source.attacks[index], target), state
    )
    item = next(gen)
    assert player.hand == drawn and len(player.prize) == 6
    try:
        while True:
            item = gen.send(list(item[3]["raw_available_actions"])[-1])
    except StopIteration:
        pass
    assert len(player.hand) == len(drawn) + 1 and state.turn == opponent.id
