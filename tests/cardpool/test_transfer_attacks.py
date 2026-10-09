from types import SimpleNamespace
from ptcg.core.card_registry import registry
from ptcg.core.enums import PokemonRule
from packages.rules.transfer_attacks import resolve
from packages.rules.field_effects import resolve as field
from tests.cardpool.test_checkup import board
from test_effects import drive, zone


def action(p, o):
    return SimpleNamespace(source=p.active[0], target=o.active[0])


def test_moving_counters_to_protected_destination_removes_them_from_donor():
    s, p, o = board()
    donor = p.bench[0]
    donor.hp -= 50
    before = o.active[0].hp
    o.active[0].attack_protection = {"turn": s.turn_number, "effects": True}
    drive(resolve({"operation": "transfer_counters", "all": True}, action(p, o), s))
    assert donor.hp == type(donor)().hp
    assert o.active[0].hp == before


def test_counter_rearrangement_has_original_budget_and_cannot_recycle_new_counters():
    s, p, o = board()
    o.bench[0].hp -= 30
    total = sum(c.hp for c in o.active + o.bench)
    seen = drive(resolve({"operation": "transfer_counters", "opponent": True, "anywhere": True}, action(p, o), s))
    assert len(seen) == 6
    assert sum(c.hp for c in o.active + o.bench) == total
    assert o.bench[0].hp == type(o.bench[0])().hp


def test_ancient_counter_transfer_cannot_choose_non_ancient():
    s, p, o = board()
    donor = p.bench[0]
    donor.hp -= 20
    before = o.active[0].hp
    rule = {"operation": "transfer_counters", "all": True, "tag": "ANCIENT"}
    drive(resolve(rule, action(p, o), s))
    assert o.active[0].hp == before
    donor.pokemonRule = PokemonRule.ANCIENT
    drive(resolve(rule, action(p, o), s))
    assert o.active[0].hp == before - 20


def test_arbitrary_energy_transfer_moves_each_physical_card_at_most_once():
    s, p, o = board()
    energies = [registry.get("SVE-008")() for _ in range(3)]
    p.active[0].attachment = energies[:]
    seen = drive(resolve({"operation": "transfer_energy"}, action(p, o), s))
    assert len(seen) == 6
    assert set(p.bench[0].attachment) == set(energies)
    assert not p.active[0].attachment


def test_gust_checks_selected_bench_protection_not_old_active_protection():
    s, p, o = board()
    old, new = o.active[0], o.bench[0]
    old.attack_protection = {"turn": s.turn_number, "effects": True}
    drive(field({"kind": "gust", "coin": False}, action(p, o), s))
    assert o.active == [new]
    # Old Active remains protected only until leaving play's Active zone;
    # explicitly apply a fresh effect to check a protected Bench recipient.
    old.attack_protection = {"turn": s.turn_number, "effects": True}
    drive(field({"kind": "gust", "coin": False}, action(p, o), s))
    assert o.active == [new]


def test_window_bench_accepts_evolution_pokemon_without_evolving_or_extra_cards():
    s, p, o = board()
    card = registry.get("P01-006")()
    zone(p, "left", [card, registry.get("SVE-008")()])
    drive(resolve({"operation": "window_bench", "count": 8}, action(p, o), s))
    assert card in p.bench and card not in p.left
    assert card.firstTurnPlayed and not getattr(card, "evolved", [])
    assert len(p.left) == 1
