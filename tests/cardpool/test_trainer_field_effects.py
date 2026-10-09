from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType, CardPosition, PokemonRule
from packages.rules.attack_restrictions import allowed
from tests.cardpool.test_shared_trainers import board, play
from test_effects import zone


def test_generator_only_attaches_window_energy_to_lightning_bench():
    s, p, o, c = board("attach_multiple", name="Electric Generator")
    bench = registry.get("P01-005")()
    bench.cardType = CardType.LIGHTNING
    zone(p, "bench", [bench])
    energies = [registry.get("SVE-004")() for _ in range(3)]
    others = [registry.get("SVE-008")() for _ in range(3)]
    zone(p, "left", [energies[0], *others, energies[1], energies[2]])
    play(s, c)
    assert set(bench.attachment) == set(energies[:2])
    assert energies[2] in p.left and not p.energyPlayedTurn
    assert not s.public_reveals


def test_sada_attaches_once_to_distinct_ancients_including_active_then_draws():
    s, p, o, c = board("attach_multiple", name="Professor Sada's Vitality")
    bench = registry.get("P01-005")()
    zone(p, "bench", [bench])
    p.active[0].pokemonRule = bench.pokemonRule = PokemonRule.ANCIENT
    energies = [registry.get("SVE-008")() for _ in range(2)]
    zone(p, "discard", list(energies))
    play(s, c)
    assert all(
        len([e for e in energies if e in h.attachment]) == 1 for h in p.active + p.bench
    )
    assert (
        next(e for e in energies if e in p.active[0].attachment).cardPosition
        == CardPosition.ACTIVE_ATTACHMENT
    )
    assert len(p.hand) == 5


def test_mela_requires_prior_knockout_and_public_energy_and_draws_to_six():
    s, p, o, c = board("attach_multiple", name="Mela")
    zone(p, "discard", [registry.get("SVE-002")()])
    p.hasPokemonDead = False
    assert not c.get_actions(s)
    p.hasPokemonDead = True
    play(s, c)
    assert len(p.hand) == 6 and len(p.active[0].attachment) > 0


def test_geeta_blocks_new_pokemon_as_well_as_existing_attackers():
    s, p, o, c = board("attach_multiple", name="Geeta")
    play(s, c)
    new = registry.get("P01-005")()
    zone(p, "active", [new])
    assert not allowed(new, new.attacks[0], s)
    s.turn_number += 2
    assert allowed(new, new.attacks[0], s)


def test_penny_returns_physical_attachments_resets_damage_and_replaces_active():
    s, p, o, c = board("return_pokemon", name="Penny")
    target, survivor = registry.get("P01-005")(), registry.get("P01-006")()
    # Only the basic active is eligible; the survivor is an evolution.
    from ptcg.core.enums import Stage

    survivor.stage = Stage.STAGE_1
    zone(p, "active", [target])
    zone(p, "bench", [survivor])
    energy = registry.get("SVE-008")()
    target.attachment = [energy]
    target.hp = 10
    before = len(o.prize)
    play(s, c)
    assert target in p.hand and energy in p.hand and p.active == [survivor]
    assert target.hp == type(target)().hp and not target.attachment
    assert len(o.prize) == before


def test_energy_switch_moves_one_physical_basic_and_preserves_manual_flag():
    s, p, o, c = board("transfer_energy", name="Energy Switch")
    target = registry.get("P01-005")()
    zone(p, "bench", [target])
    energy = registry.get("SVE-008")()
    p.active[0].attachment = [energy]
    play(s, c)
    assert energy in target.attachment and not p.active[0].attachment
    assert not p.energyPlayedTurn


def test_crushing_hammer_tails_does_not_discard(monkeypatch):
    from ptcg.core.enums import Coin

    s, p, o, c = board("discard_field_energy", name="Crushing Hammer")
    energy = registry.get("SVE-008")()
    o.active[0].attachment = [energy]
    monkeypatch.setattr(
        "packages.rules.trainer_field_effects.flip_coin", lambda s: Coin.TAIL
    )
    play(s, c)
    assert energy in o.active[0].attachment and c in p.discard
