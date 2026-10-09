"""Admission and physical effects for the user's six source confirmations."""
import pytest

from packages.rules.adapter import Adapter
from packages.rules.catalog import Catalog
from packages.collection.domain import CARDS
from ptcg.core.action import AttackAction
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType, PokemonRule
from test_effects import context, drive, zone


PRINTINGS = ['CN:CHS:13927', 'CN:CHS:15983', 'CN:CHS:17304',
             'CN:CHS:17375', 'CN:CHS:17377', 'CN:CSVM2bC:028',
             'CN:CSVM2cC:027', 'CN:CHS:17995']


@pytest.mark.parametrize('printing', PRINTINGS)
def test_confirmed_printing_admission(printing):
    Adapter(0)
    card = CARDS[printing]
    assert card['sourceVerified'] and card['effectStatus'] == 'verified'
    assert registry.get(card['engineId'])
    assert Catalog(CARDS).admit([
        {'printingId': printing, 'quantity': 1},
        {'printingId': 'CN:CSVM2cC:004', 'quantity': 4},
        {'printingId': 'CN:CSM2.1C:044', 'quantity': 55},
    ])


def test_confirmed_retreat_and_trainer_traits():
    Adapter(0)
    assert registry.get(CARDS[PRINTINGS[0]]['engineId'])().retreat == [CardType.COLORLESS]
    for printing in PRINTINGS[2:7]:
        expected = PokemonRule.ANCIENT if printing == 'CN:CHS:17375' else PokemonRule.FUTURE
        entries = [{'printingId': printing, 'quantity': 1},
                   {'printingId': 'CN:CSVM2cC:004', 'quantity': 4},
                   {'printingId': 'CN:CSM2.1C:044', 'quantity': 55}]
        match = Catalog(CARDS).create_match(entries, entries)
        cards = match.env._deck1_cards.cards
        actual = next(c for c in cards if c.id == CARDS[printing]['engineId'])
        assert actual.pokemonRule == expected


@pytest.mark.parametrize('has_bench,choose_none', [(True, False), (True, True), (False, False)])
def test_magnemite_recovers_only_basic_lightning_to_one_bench(has_bench, choose_none):
    s, p, o = context()
    source = registry.get(CARDS['CN:CHS:15983']['engineId'])()
    zone(p, 'active', [source])
    benches = [registry.get('P01-005')(), registry.get('P01-005')()] if has_bench else []
    zone(p, 'bench', benches)
    energies = [registry.get('SVE-004')() for _ in range(3)]
    fire = registry.get('SVE-002')()
    from packages.rules.additions import LuminousEnergy
    special = LuminousEnergy()
    zone(p, 'discard', [*energies, fire, special])
    action = AttackAction(p.id, source, source.attacks[0], o.active[0])
    picker = (lambda actions, info, step: list(actions)[0]) if choose_none else None
    drive(source.reduce_action(action, s), picker)
    attached = [e for b in benches for e in b.attachment]
    if has_bench and not choose_none:
        assert len(attached) == 2 and all(e in energies for e in attached)
        assert sorted(len(b.attachment) for b in benches) == [0, 2]
    else:
        assert not attached
    assert fire in p.discard and special in p.discard and not source.attachment
    assert set(attached + p.discard) == set(energies + [fire, special])


def test_charmander_discards_attached_energy():
    s, p, o = context()
    source = registry.get(CARDS['CN:CHS:17995']['engineId'])()
    zone(p, 'active', [source])
    from ptcg.core.enums import CardPosition
    energy = registry.get('SVE-002')()
    source.attachment = [energy]
    energy.cardPosition = CardPosition.ACTIVE_ATTACHMENT
    energy.index = 1
    o.active[0].hp = 300
    drive(source.reduce_action(AttackAction(p.id, source, source.attacks[0], o.active[0]), s))
    assert energy in p.discard and not source.attachment
