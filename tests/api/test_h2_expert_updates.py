"""Synthetic public boards for the accepted v7/v9 product updates."""
from copy import deepcopy

import pytest

from apps.api.expert_policies import dragapult, grimmsnarl, registry
from packages.simulation.a2 import legal


def card(name, ref, hp=100, prize=1, **kwargs):
    return dict(name=name, ref=ref, hp=hp, maximumHp=hp, prize=prize,
                energy=[], attacks=[], abilities=[], retreatCost=[], **kwargs)


def side(active, bench, hand=None, prizes=6):
    return dict(active=active, bench=bench, hand=hand, hand_count=len(hand or []),
                prize_count=prizes, deck_count=20, discard=[], lost_zone=[], turnFlags={})


def draw_board():
    # Use the actual supported ability definition; the rest is a synthetic board.
    source_id = next(key for key, spec in dragapult.tempo.risk.SPECS.items()
                     if spec['name'] == 'Dusknoir' and any(
                         a.get('selfKnockout') and a.get('effect', {}).get('amount') == 130
                         for a in spec.get('abilities', [])))
    own = side([card('Budew', 'own-active')],
               [card('Dusknoir', 'blast', 150, id=source_id)], [], 2)
    other = side([card('Synthetic attacker ex', 'enemy-active', 300, 2)],
                 [card('Synthetic reserve ex', 'enemy-reserve', 120, 2)], None, 1)
    return dict(stateVersion=9, phase='playing', done=False,
                observation=dict(self=own, opponent=other, stadium=[], turn='PLAYER2', turn_number=5),
                decision=dict(id='d9', actor='PLAYER2', kind='options', options=[
                    dict(id='blast', actionType='UseAbilityAction', source='Dusknoir', sourceRef='blast'),
                    dict(id='pass', actionType='PassTurn')]))


def choose(policy, dto):
    before = deepcopy(dto)
    command, detail = policy.decide(dto, entries=policy.PROFILE['entries'],
                                    environment=policy.PROFILE['environmentHash'])
    assert dto == before and legal(dto, command) and not detail['fallback']
    return command, detail


def test_draw_ability_is_kept_and_a_real_winning_attack_has_priority():
    dto = draw_board()
    command, detail = choose(dragapult, dto)
    assert command['choice']['optionId'] == 'blast'
    assert detail['reason'] == 'executable-draw-plan'
    attack = dict(name='Phantom Dive', damage=300, cost=[])
    dto['observation']['self']['active'][0]['attacks'] = [attack]
    dto['decision']['options'].append(dict(id='win', actionType='AttackAction', attack=attack))
    command, detail = choose(dragapult, dto)
    assert command['choice']['optionId'] == 'win'
    assert detail['reason'] == 'executable-terminal-plan'


def test_losing_or_protected_blast_is_not_claimed_as_a_draw():
    dto = draw_board()
    own, other = dto['observation']['self'], dto['observation']['opponent']
    source = own['bench'][0]
    assert dragapult.tempo.drawing_blast_target(own, other, source)['ref'] == 'enemy-reserve'
    own['prize_count'] = 3
    assert dragapult.tempo.drawing_blast_target(own, other, source) is None
    own['prize_count'] = 2
    other['bench'][0]['abilityEffectProtected'] = True
    assert dragapult.tempo.drawing_blast_target(own, other, source) is None


@pytest.mark.parametrize('own_prizes,other_prizes,enemy_active_hp,expected', [
    (2, 1, 300, .5), (3, 1, 300, 0), (2, 2, 300, 1), (3, 2, 300, None),
    (2, 1, 0, 1),
])
def test_simultaneous_win_conditions(own_prizes, other_prizes, enemy_active_hp, expected):
    dto = draw_board()
    own, other = dto['observation']['self'], dto['observation']['opponent']
    own['prize_count'], other['prize_count'] = own_prizes, other_prizes
    own['bench'][0]['hp'] = other['bench'][0]['hp'] = 0
    other['active'][0]['hp'] = enemy_active_hp
    assert dragapult.tempo.terminal_score(own, other) == expected


def test_grimmsnarl_preserves_concrete_preparation_before_nonterminal_gust():
    attacker = card(grimmsnarl.MAIN, 'main', 320, 2)
    attacker.update(energy=['DARK', 'DARK'],
                    attacks=[dict(name='Shadow Bullet', damage=180, cost=['DARK', 'DARK'])])
    own = side([attacker], [card(grimmsnarl.BASIC, 'reserve')], [], 6)
    other = side([card('Synthetic defender', 'front', 200)],
                 [card('Synthetic reserve', 'back', 100)], None, 6)
    gust = dict(id='gust', actionType='UseSupporterAction', source="Boss's Orders")
    evolution = dict(id='evolve', actionType='EvolvePokemonAction', source=grimmsnarl.MID,
                     targetRef='reserve')
    dto = dict(observation=dict(self=own, opponent=other),
               decision=dict(options=[gust, evolution]))
    before = deepcopy(dto)
    assert grimmsnarl.prep_action(dto, gust, evolution) == evolution
    assert dto == before


def test_shipped_routes_have_exact_accepted_versions(tmp_path):
    expected = {
        'h2-marnie-grimmsnarl': 'h2-grimmsnarl-exchange-plan-v7',
        'h2-dragapult-dusknoir': 'h2-dragapult-draw-development-v9',
        'h2-gardevoir-ex': 'h2-gardevoir-basic-v5',
        'h2-n-zoroark-ex': 'h2-zoroark-basic-v2',
        'h2-joltik-box': 'h2-joltik-basic-v1',
    }
    for preset, version in expected.items():
        policy = registry.POLICIES[preset]
        pin = registry.select(policy.PROFILE['entries'], tmp_path / 'test.sqlite')
        assert pin['version'] == version


@pytest.mark.parametrize('policy', [grimmsnarl, dragapult])
def test_updated_policies_keep_the_hidden_hand_boundary(policy):
    dto = draw_board()
    dto['observation']['opponent']['hand'] = []
    with pytest.raises(ValueError, match='HIDDEN_HAND_EXPOSED'):
        choose(policy, dto)
