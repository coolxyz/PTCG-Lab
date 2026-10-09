"""Acceptance reads constructed engine cards, independently of catalogue flags."""
from pathlib import Path
from collections import Counter
import pytest
from packages.sync.common import read
from packages.battle import runtime
from ptcg.core.card_registry import registry

ROOT=Path(__file__).resolve().parents[2]
PROOFS=read(ROOT/'data/sync/reviewed-equivalence.json')['proofs']


@pytest.mark.parametrize('rule_hash',list(PROOFS))
def test_actual_engine_card_matches_reviewed_source(rule_hash):
    proof=PROOFS[rule_hash]; f=proof['face']
    cls=registry.get(proof['effectKey'])
    assert cls is not None
    card=cls()
    if proof['method']=='native-identical-source-face':
        line=f"1 {card.name} {card.set_name} {card.number}"
        deck=[line,'4 Mew ex MEW 151','55 Metal Energy SVE 008'] if card.id!='MEW-151' else [line,'59 Metal Energy SVE 008']
        game=runtime.Adapter(0,deck,deck)
        card=next(c for c in game.env.gamestate.player1.deck if c.id==proof['effectKey'])
    if f['category']=='能量':
        assert card.superType.name=='ENERGY'
        assert card.energyType.name==('BASIC' if f['energyType']=='基本能量' else 'SPECIAL')
        if f['energyType']=='基本能量':
            assert card.cardType.name==f['type']
            assert [t.name for t in card.provides]==[f['type']]
    elif f['category']=='宝可梦':
        assert card.hp==f['hp']
        assert card.stage.name==f['stage']
        assert card.cardType.name==f['type']
        assert len(card.retreat)==f['retreat']
        assert [t.name for t in card.weakness]==([f['weakness']] if f['weakness'] else [])
        assert [t.name for t in card.resistance]==([f['resistance']] if f['resistance'] else [])
        assert card.prize==(2 if f['pokemonType']=='宝可梦ex' else 1)
        assert len(card.attacks)==len(f['attacks'])
        for actual,printed in zip(card.attacks,f['attacks']):
            assert Counter(t.name for t in actual.cost)==Counter(printed['cost'])
    else:
        assert card.superType.name=='TRAINER'
    tags=(f['specialCard'] or '').split('|')
    assert bool(getattr(card,'aceSpec',False))==('ACE SPEC' in tags)
    expected=next(({'古代':'ANCIENT','未来':'FUTURE','太晶':'TERA'}[t] for t in tags if t in ('古代','未来','太晶')),None)
    if expected:
        assert getattr(getattr(card,'pokemonRule',None),'name',None)==expected
