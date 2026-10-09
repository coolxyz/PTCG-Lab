import copy,json
from pathlib import Path
import pytest
from packages.rules.catalog import Catalog,AdmissionError,select_image
from packages.engine_adapter.cn_format import validate
from packages.rules.adapter import Adapter
from packages.rules.agents import DTOAgent
from packages.rules.invariants import check_conservation
ROOT=Path(__file__).resolve().parents[2]
DATA=json.loads((ROOT/'tests/fixtures/engine-catalog.json').read_text(encoding='utf-8'))

@pytest.mark.parametrize('name',['gholdengo','dragapult'])
def test_actual_deck_admission(name):
    catalog=Catalog(DATA['printings']);entries=DATA['decks'][name]
    assert sum(e['quantity'] for e in entries)==60
    assert validate(catalog.resolve(entries))['playable']
    game=catalog.create_match(entries,entries)
    agent=DTOAgent(99)
    while game.env.start_stage:
        game.submit(game.actor,agent.predict(game.view(game.actor)))
    check_conservation(game.env.gamestate)


def test_exact_versions_and_collision():
    p=DATA['printings']
    assert p['CN:CSVM2cC:004']['engineId']=='P01-005'
    assert p['CN:CSVM2cC:008']['engineId']=='P01-006'
    assert p['CN:CSVM2cC:026']['cnName']=='奇树'
    assert p['CN:CSVM2cC:029']['sourceCorrection']['original']=='026'
    assert p['CN:basic:MET']['collectorNumber'] is None
    assert p['CN:basic:MET']['identityKind']=='basic-energy'


def test_combined_ace_specs_rejected_by_catalog():
    entries=copy.deepcopy(DATA['decks']['dragapult'])
    entries[-1]['quantity']-=1
    entries.append({'printingId':'CN:CSVM2cC:018','quantity':1})
    with pytest.raises(AdmissionError,match='ACE_SPEC_LIMIT'):Catalog(DATA['printings']).admit(entries)


def test_date_gate_and_unreviewed_effect_closed():
    catalog=Catalog(DATA['printings']);entries=DATA['decks']['gholdengo']
    with pytest.raises(AdmissionError,match='NOT_RELEASED'):catalog.admit(entries,'2026-07-15')
    cards=copy.deepcopy(DATA['printings']);cards[entries[0]['printingId']]['effectStatus']='unverified'
    with pytest.raises(AdmissionError,match='EFFECT_NOT_VERIFIED'):Catalog(cards).admit(entries)

@pytest.mark.parametrize('fact',json.loads((ROOT/'data/engine/expected-pokemon-facts.json').read_text()))
def test_card_numeric_fields_against_cn_source(fact):
    from ptcg.core.card_registry import registry
    card=registry.get(fact['engineId'])()
    got={'hp':card.hp,'retreatCount':len(card.retreat),'type':card.cardType.name,
         'weakness':[c.name for c in card.weakness],'resistance':[c.name for c in card.resistance],
         'attackCosts':[[c.name for c in a.cost] for a in card.attacks]}
    assert got==fact['expected']


def test_complete_dragon_evolution_chain_is_available():
    from packages.rules.effects import Dragapult,Drakloak
    from ptcg.core.card_registry import registry
    from ptcg.utils.utils import check_evolve,current_player
    from ptcg.core.enums import PokemonPosition,CardPosition
    from test_rules import finish_setup
    game=Adapter();finish_setup(game);state=game.env.gamestate;p=current_player(state)
    base=registry.get('TWM-128')();middle=Drakloak();top=Dragapult()
    p.active=[base];p.bench=[];base.firstTurnPlayed=False
    assert check_evolve(middle,state)==[base]
    p.active=[middle];middle.firstTurnPlayed=False
    assert check_evolve(top,state)==[middle]


def test_provenance_survives_private_replay_without_opponent_deck_leak():
    entries=DATA['decks'];game=Catalog(DATA['printings']).create_match(entries['gholdengo'],entries['dragapult'],seed=7)
    provenance=game.export_private_replay()['config']['provenance']
    assert provenance['deck2']==entries['dragapult']
    public=game.view(game.actor)
    assert public['catalogVersion']==provenance['catalogVersion']
    assert 'provenance' not in public and 'deck2' not in public
    assert Adapter.replay(game.export_private_replay()).config==game.config


def test_reviewed_traditional_image_preserves_cn_printing():
    card=DATA['printings']['CN:CSVM2cC:004']
    assert select_image(card)['locale']=='zh-Hant'
    assert card['language']=='zh-Hans' and card['collectorNumber']=='004'
