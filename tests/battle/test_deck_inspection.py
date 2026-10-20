"""Full search visibility, privacy, and identical AI observation contracts."""
import json
import pytest
from packages.battle.runtime import Adapter, view
from packages.rules.entry_effects import effect, choice
from ptcg.core.card_registry import registry
from ptcg.utils.utils import current_player, opponent_player
from test_rules import finish_setup
from test_effects import zone


def fixture():
    game = Adapter(7)
    finish_setup(game)
    state = game.env.gamestate
    p, o = current_player(state), opponent_player(state)
    source = registry.get('PAF-091')()  # Ultra Ball, source metadata only
    zone(p, 'left', [registry.get('SVE-008')(), registry.get('SVE-008')(), source])
    zone(p, 'prize', [registry.get('SVE-005')()])
    return game, state, p, o, source


def at_choice(game, gen):
    item = next(gen)
    game.obs, _, game.done, game.info = item
    return item[3]['raw_available_actions']


def test_full_filtered_search_includes_ineligible_cards_and_ai_gets_same_view():
    game, state, p, o, source = fixture()
    gen = effect(source, {'kind':'search', 'filter':'basic_energy', 'count':1}, state)
    actions = at_choice(game, gen)
    dto = view(game, p.id)
    d = dto['decision']
    assert len(d['candidates']) == 2
    deck = d['deckInspection']
    assert deck['total'] == 3 and deck['ordered'] is False
    assert sorted(e['count'] for e in deck['cards']) == [1, 2]
    assert any(e['card']['id'] == source.id for e in deck['cards'])
    assert 'SVE-005' not in json.dumps(deck)  # Hidden prize never exported.
    assert view(game, o.id)['decision'] is None
    candidates = d['candidates']
    p.left.reverse()
    assert view(game,p.id)['decision']['deckInspection'] == deck
    assert view(game,p.id)['decision']['candidates'] == candidates
    assert not any(k in e['card'] for e in deck['cards'] for k in ('index','ref','cardPosition'))
    # Browsing does not mutate legal candidate selection.
    with pytest.raises(StopIteration):
        gen.send(actions.action_for_candidate_indices([0]))
    assert len(p.left) == 2


@pytest.mark.parametrize('kind', ['top', 'discard'])
def test_partial_views_and_recovery_do_not_grant_deck_visibility(kind):
    game, state, p, o, source = fixture()
    if kind == 'top':
        gen = choice(source, p.left[:1], state, 0, 1)
    else:
        zone(p, 'discard', [registry.get('SVE-008')()])
        gen = effect(source, {'kind':'recover','filter':'basic_energy','count':1},state)
    at_choice(game, gen)
    assert 'deckInspection' not in view(game,p.id)['decision']


def test_no_eligible_cards_still_allows_inspection_and_continue():
    game,state,p,o,source=fixture()
    gen=effect(source,{'kind':'search','filter':'pokemon','count':2},state)
    actions=at_choice(game,gen)
    d=view(game,p.id)['decision']
    assert d['min']==d['max']==0 and d['candidates']==[]
    assert d['deckInspection']['total']==3
    with pytest.raises(StopIteration):gen.send(actions.action_for_candidate_indices([]))


def test_opponent_deck_search_is_visible_only_to_actor():
    game,state,p,o,source=fixture()
    gen=effect(source,{'kind':'search','filter':'pokemon','count':1,'opponent':True},state)
    at_choice(game,gen)
    assert view(game,p.id)['decision']['deckInspection']['owner']=='opponent'
    assert view(game,o.id)['decision'] is None


def test_ultra_ball_payment_does_not_reveal_deck_before_search():
    from ptcg.core.action import UseItemAction
    game,state,p,o,source=fixture()
    zone(p,'hand',[source,registry.get('SVE-005')(),registry.get('SVE-008')()])
    zone(p,'left',[registry.get('P01-005')(),registry.get('SVE-008')()])
    gen=source.reduce_action(UseItemAction(p.id,source),state)
    actions=at_choice(game,gen)
    assert 'deckInspection' not in view(game,p.id)['decision']
    item=gen.send(actions.action_for_candidate_indices([0,1]))
    game.obs,_,game.done,game.info=item
    assert view(game,p.id)['decision']['deckInspection']['total']==2


def test_legacy_pidgeot_search_is_explicitly_granted():
    from ptcg.core.action import UseAbilityAction
    game,state,p,o,_=fixture()
    bird=registry.get('OBF-164')()
    zone(p,'active',[bird])
    gen=bird.reduce_action(UseAbilityAction(p.id,bird,bird.ability[0]),state)
    at_choice(game,gen)
    assert view(game,p.id)['decision']['deckInspection']['total']==3


def test_hidden_choice_cannot_grant_full_deck_inspection():
    from packages.rules.deck_search import choose_card_actions
    _, _, p, _, source = fixture()
    with pytest.raises(ValueError, match='hidden choice'):
        choose_card_actions(p.id, p.id, 0, 1, list(p.left),
                            hidden=True, source=source, deck_owner=p)


@pytest.mark.parametrize('preset', [
    'h2-marnie-grimmsnarl', 'h2-gardevoir-ex', 'h2-dragapult-dusknoir',
    'h2-n-zoroark-ex', 'h2-joltik-box',
])
def test_existing_h2_experts_accept_the_new_observation(preset, tmp_path):
    from apps.api.expert_policies import registry as experts
    from packages.battle import h2
    from packages.simulation.a2 import legal
    game, state, p, _, source = fixture()
    gen = effect(source, {'kind': 'search', 'filter': 'basic_energy', 'count': 1}, state)
    at_choice(game, gen)
    dto = view(game, p.id)
    assert dto['decision']['deckInspection']['total'] == 3
    command, detail = h2.decide(dto)
    assert legal(dto, command) and not detail['fallback']
    policy = experts.POLICIES[preset]
    pin = experts.select(policy.PROFILE['entries'], tmp_path / 'test.sqlite')
    assert pin is not None and pin['id'] == preset
    command, detail = experts.decide(dto, pin)
    assert legal(dto, command) and not detail['fallback']
