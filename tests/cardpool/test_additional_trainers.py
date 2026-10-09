import random
import pytest
from ptcg.core.card_registry import registry
from tests.cardpool.test_shared_trainers import board, play
from test_effects import zone


@pytest.mark.parametrize("heads", [True, False])
def test_poke_ball_coin_uses_seed_and_discards_card_before_search(heads):
    s, p, o, c = board("search_hand", name="Poké Ball")
    pokemon = registry.get("P01-005")()
    zone(p, "left", [pokemon])
    s.rng = random.Random(1 if heads else 0)
    play(s, c)
    assert c in p.discard and (pokemon in p.hand) == heads
    assert bool(s.public_reveals) == heads


def test_bills_transfer_keeps_nonpokemon_and_reveals_only_selected_cards():
    s, p, o, c = board("look_hand", name="Bill's Transfer")
    pokemon = [registry.get("P01-005")() for _ in range(3)]
    energies = [registry.get("SVE-002")() for _ in range(6)]
    zone(p, "left", [*pokemon, *energies])
    play(s, c)
    assert all(x in p.hand for x in pokemon)
    assert set(p.left) == set(energies) and not any(e in p.hand for e in energies)
    assert s.public_reveals


def test_atticus_requires_poison_and_battle_milk_requires_more_prizes():
    s, p, o, c = board("shuffle_draw", name="Atticus")
    assert not c.get_actions(s)
    o.active[0].poisoned = True
    assert c.get_actions(s)
    play(s, c)
    assert len(p.hand) == 7
    s, p, o, c = board("heal", name="Fighting Au Lait")
    p.active[0].hp -= 80
    assert not c.get_actions(s)
    o.prize.pop()
    assert c.get_actions(s)


def test_n_energy_item_filters_recipients_and_does_not_use_manual_attachment():
    s, p, o, c = board("attach_multiple", name="N's PP Up")
    x, y = registry.get("P01-005")(), registry.get("P01-005")()
    x.name, y.name = "N's Zorua", "Zorua"
    zone(p, "bench", [x, y])
    energy = registry.get("SVE-002")()
    zone(p, "discard", [energy])
    play(s, c)
    assert energy in x.attachment and not y.attachment and not p.energyPlayedTurn


def test_terminal_board_does_not_generate_opponent_target_actions_after_last_knockout():
    s, p, o, c = board("opponent_status")
    o.active, o.bench = [], []
    assert not c.get_actions(s)
    assert not p.get_actions(s)
