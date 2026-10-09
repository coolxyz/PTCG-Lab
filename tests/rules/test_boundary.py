import copy
import json
import random
import subprocess
import sys
import pytest
from ptcg.core.enums import PlayerId
from ptcg.core.action import ChooseCardActionSpace
from packages.rules.adapter import Adapter
from packages.rules.agents import DTOObserver, DTOAgent
from packages.rules.session import Session
from packages.rules.catalog import Catalog, AdmissionError, select_image
from packages.engine_adapter.base import RejectedCommand, policy


def test_observer_rejects_raw_state():
    game = Adapter()
    with pytest.raises(TypeError, match='PLAYER_DTO_REQUIRED'):
        DTOObserver().observe(game.env.gamestate)


def test_observer_message_and_command_wiring():
    game = Adapter()
    view = game.view(game.actor)
    assert json.loads(DTOObserver().build_user_message(view)) == view
    game.submit(game.actor, DTOAgent().predict(view))
    assert game.version == 1


@pytest.mark.parametrize('case', ['perspective', 'turn', 'own_hand', 'opponent_hand', 'prizes',
                                   'stadium', 'serializable', 'selection_flag', 'pokemon_fields'])
def test_replacement_observer_contract(case):
    from test_rules import finish_setup
    game = Adapter(42)
    finish_setup(game)
    view = DTOObserver().observe(game.view(PlayerId.PLAYER2))
    obs = view['observation']
    if case == 'perspective': assert obs['viewer'] == 'player2'
    elif case == 'turn': assert obs['turn'] == game.env.gamestate.turn.name.lower()
    elif case == 'own_hand': assert len(obs['self']['hand']) == obs['self']['hand_count']
    elif case == 'opponent_hand': assert obs['opponent']['hand'] is None
    elif case == 'prizes': assert obs['self']['prize_count'] == obs['opponent']['prize_count'] == 6
    elif case == 'stadium': assert obs['stadium'] == []
    elif case == 'serializable': assert json.loads(json.dumps(view)) == view
    elif case == 'selection_flag': assert obs['is_choosing_card'] is False
    elif case == 'pokemon_fields': assert {'hp','damage_counters','attacks','stage'} - {'damage_counters'} <= set(obs['self']['active'][0])


def test_out_of_turn_hidden_choice_and_noninterference():
    game = Adapter(7)
    game.env.gamestate.turn = PlayerId.PLAYER1
    candidates = list(game.env.gamestate.player1.left[:6])
    game.info['raw_available_actions'] = ChooseCardActionSpace(PlayerId.PLAYER2, PlayerId.PLAYER1, 1, 1, candidates, hidden=True)
    view = game.view(PlayerId.PLAYER2)
    assert view['decision']['actor'] == 'PLAYER2'
    assert all(set(c) == {'ref'} for c in view['decision']['candidates'])
    game.actions.candidates.reverse()
    assert game.view(PlayerId.PLAYER2) == view
    assert game.view(PlayerId.PLAYER1)['decision'] is None


def test_own_view_is_detached():
    game = Adapter(0)
    before = game.private_digest()
    view = game.view(game.actor)
    view['setupEvents'].clear()
    view['decision']['options'].clear()
    assert game.private_digest() == before


@pytest.mark.parametrize('mutation', ['stale', 'wrong_actor', 'invalid_option', 'reuse_id'])
def test_rejection_preserves_state(mutation):
    game = Adapter(0)
    actor = game.actor
    cmd = DTOAgent().predict(game.view(actor))
    if mutation == 'stale': cmd['expectedStateVersion'] = -1
    elif mutation == 'wrong_actor': actor = next(p for p in PlayerId if p != actor)
    elif mutation == 'invalid_option': cmd['choice']['optionId'] = 'invalid'
    elif mutation == 'reuse_id':
        game.submit(actor, cmd)
        cmd['choice'] = {}
    before = game.private_digest()
    with pytest.raises(RejectedCommand): game.submit(actor, cmd)
    assert game.private_digest() == before


def test_cross_process_replay(tmp_path):
    game = Adapter(7)
    agent = DTOAgent(13)
    for _ in range(80):
        if game.done: break
        game.submit(game.actor, agent.predict(game.view(game.actor)))
    record = game.export_private_replay()
    script = '''import json,sys
from loguru import logger
from packages.rules.adapter import Adapter
logger.remove()
g=Adapter.replay(json.load(sys.stdin))
print(json.dumps({'digest':g.private_digest(),'views':[g.view(p.id) for p in g.env.get_players()]}))
'''
    child = subprocess.run([sys.executable, '-c', script], input=json.dumps(record), text=True, capture_output=True, check=True)
    result = json.loads(child.stdout)
    assert result['digest'] == game.private_digest()
    assert result['views'] == [game.view(p.id) for p in game.env.get_players()]


def test_corrupt_and_different_version_replay_rejected():
    game = Adapter()
    record = game.export_private_replay()
    record['stateDigest'] = 'corrupt'
    with pytest.raises(ValueError, match='REPLAY_STATE_MISMATCH'): Adapter.replay(record)
    record['implementation'] = 'other'
    with pytest.raises(ValueError, match='ENGINE_VERSION_MISMATCH'): Adapter.replay(record)


def test_restart_preserves_idempotent_ack(tmp_path):
    path = tmp_path / 'session.sqlite'
    session = Session(path)
    actor = session.game.actor
    cmd = DTOAgent().predict(session.game.view(actor))
    receipt = session.submit(actor, cmd)
    session.close()
    resumed = Session(path)
    assert resumed.submit(actor, cmd) == receipt
    assert resumed.game.version == 1
    resumed.close()


def test_failed_persistence_rolls_back(tmp_path, monkeypatch):
    session = Session(tmp_path / 'session.sqlite')
    before = session.game.private_digest()
    def fail(): raise OSError('injected disk failure')
    monkeypatch.setattr(session, '_save', fail)
    cmd = DTOAgent().predict(session.game.view(session.game.actor))
    with pytest.raises(OSError): session.submit(session.game.actor, cmd)
    assert session.game.private_digest() == before
    assert session.game.version == 0
    session.close()


def test_failed_engine_step_rolls_back(tmp_path, monkeypatch):
    session = Session(tmp_path / 'session.sqlite')
    before = session.game.private_digest()
    original = session.game.env.step
    def fail(action):
        original(action)
        raise RuntimeError('injected after mutation')
    monkeypatch.setattr(session.game.env, 'step', fail)
    cmd = DTOAgent().predict(session.game.view(session.game.actor))
    with pytest.raises(RuntimeError): session.submit(session.game.actor, cmd)
    assert session.game.private_digest() == before
    session.close()


def test_client_cannot_self_certify_printing():
    catalog = Catalog({})
    with pytest.raises(AdmissionError, match='CLIENT_METADATA_FORBIDDEN'):
        catalog.admit([{'printingId': 'fake', 'quantity': 60, 'sourceVerified': True}])
    with pytest.raises(AdmissionError, match='UNKNOWN_PRINTING'):
        catalog.admit([{'printingId': 'fake', 'quantity': 60}])


def test_unverified_candidates_cannot_enter_match():
    card = {'printingId': 'candidate', 'sourceVerified': False}
    with pytest.raises(AdmissionError):
        Catalog({'candidate': card}).create_match([{'printingId':'candidate','quantity':60}], [])


def test_image_fallback_does_not_modify_identity():
    card = {'printingId': 'CN-ID', 'language': 'zh-Hans', 'images': [
        {'locale':'zh-Hant', 'equivalenceReviewed':True, 'url':'https://example.test/card', 'source':'test'}]}
    original = copy.deepcopy(card)
    assert select_image(card)['label'] == '繁中卡图'
    assert card == original
    card['images'][0]['equivalenceReviewed'] = False
    assert select_image(card)['url'] is None
