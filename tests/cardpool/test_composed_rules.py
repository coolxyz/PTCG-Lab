from scripts.cardpool.compile_plain import coin_rule, compile_basic_energies


def test_sequence_preserves_order_and_rejects_unrecognized_extra_clauses():
    p = {'damage': '100', 'cost': '无',
         'eeffect': "This Pokémon also does 20 damage to itself. During your next turn, this Pokémon can't attack.",
         'effectZHS': '给这只宝可梦也造成20伤害。在下一个自己的回合，这只宝可梦无法使用招式。'}
    assert [s['kind'] for s in coin_rule(p)['steps']] == ['recoil', 'attack_lock']
    assert coin_rule({**p, 'effectZHS': p['effectZHS'].replace('20', '30')}) is None
    assert coin_rule({**p, 'eeffect': p['eeffect'] + ' You win the game.'}) is None


def test_basic_energy_requires_matching_type_name_numbered_source_and_release():
    c = {'printingId': 'CN:TEST:001', 'productCode': 'TEST', 'collectorNumber': '001',
         'cnName': '基本草能量', 'cardPage': '基本草能量（TCG）', 'basicEnergyType': 'grass',
         'catalogStatus': 'listed', 'sourceRow': 1, 'sourceEvidence': ['product-page'],
         'releasedAt': '2026-01-01', 'category': '能量'}
    article = {'text': '{{能量卡信息/header|type=草|name=基本草能量|base=y}}', 'revision': 123}
    def compile(row, page=article):
        return compile_basic_energies({'asOf': '2026-10-01', 'cards': [row]}, {c['cardPage']: page})
    assert compile(c)[0]['effectKey'] == 'P4E-001'
    assert not compile({**c, 'catalogStatus': 'source-conflict'})
    assert not compile({**c, 'releasedAt': '2027-01-01'})
    assert not compile({**c, 'basicEnergyType': 'fire'})
    assert not compile({**c, 'sourceEvidence': []})
    assert not compile(c, {**article, 'text': article['text'].replace('base=y', 'base=n')})
