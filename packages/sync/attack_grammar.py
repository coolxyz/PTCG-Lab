"""Closed Chinese clauses compiled to existing, tested engine primitives.

Every branch consumes the entire printed clause. Unmatched conditions are never
discarded. This module does not publish cards or grant battle admission.
"""
import re
from .normalize import ELEMENTS


def compile_clause(t):
    match=re.fullmatch(r'(抛掷1次硬币，?如果为正面，?则)?令对手的战斗宝可梦陷入(混乱|灼伤|中毒|睡眠|麻痹)状态。',t)
    if match:
        return {'kind':'special_status','target':'opponent','coin':bool(match[1]),'status':{'混乱':'CONFUSED','灼伤':'BURNED','中毒':'POISONED','睡眠':'ASLEEP','麻痹':'PARALYZED'}[match[2]]}
    match=re.fullmatch(r'抛掷([1-9]\d*)次硬币，(追加)?造成正面次数×([1-9]\d*)伤害。',t)
    if match:
        return {'kind':'coin_count','count':int(match[1]),'perHead':int(match[3]),**({'mode':'add'} if match[2] else {})}
    match=re.fullmatch(r'抛掷硬币直到出现反面，(追加)?造成正面次数×([1-9]\d*)伤害。',t)
    if match:
        return {'kind':'coin_until_tails','perHead':int(match[2]),**({'mode':'add'} if match[1] else {})}
    match=re.fullmatch(r'抛掷1次硬币，?如果为正面，?则追加造成([1-9]\d*)伤害。',t)
    if match:return {'kind':'coin_bonus','bonus':int(match[1])}
    match=re.fullmatch(r'回复这只宝可梦「([1-9]\d*)」(?:点)?HP。',t)
    if match:return {'kind':'heal_self','amount':int(match[1])}
    match=re.fullmatch(r'选择这只宝可梦身上附着的([1-9]\d*)个([草火水雷超斗恶钢])?能量，放于弃牌区。',t)
    if match:return {'kind':'discard_self_energy','count':int(match[1]),**({'type':ELEMENTS[match[2]]} if match[2] else {})}
    match=re.fullmatch(r'在下一个自己的回合，这只宝可梦无法使用「(.+)」。',t)
    if match:return {'kind':'named_attack_lock','name':match[1],'duration':'next'}
    match=re.fullmatch(r'将自己牌库上方的?([1-9]\d*)张卡牌放于弃牌区。',t)
    if match:return {'kind':'mill_self','count':int(match[1])}
    match=re.fullmatch(r'给对手的([1-9]\d*)只备战宝可梦，也各?造成([1-9]\d*)伤害。\[备战宝可梦不计算弱点、抗性。\]',t)
    if match:return {'kind':'bench_damage','count':int(match[1]),'amount':int(match[2])}
    match=re.fullmatch(r'给对手的1只(备战)?宝可梦，?造成([1-9]\d*)伤害。\[备战宝可梦不计算弱点、抗性。\]',t)
    if match:return {'kind':'target_damage','zone':'bench' if match[1] else 'all','count':1,'amount':int(match[2])}
    match=re.fullmatch(r'给对手的1只宝可梦身上，放置([1-9]\d*)个伤害指示物。',t)
    if match:return {'kind':'place_counters','zone':'all','select':True,'amount':int(match[1])*10}
    match=re.fullmatch(r'选择自己牌库中(?:最多|的)([1-9]\d*)张基础宝可梦，放于备战区。并重洗牌库。',t)
    if match:return {'kind':'search_bench','count':int(match[1])}
    match=re.fullmatch(r'选择自己牌库中(?:最多|的)([1-9]\d*)张「?基本([草火水雷超斗恶钢])?能量」?，在给对手看过之后，加入手牌。并重洗牌库。',t)
    if match:return {'kind':'search_hand','count':int(match[1]),'filter':'basic_energy'+(':'+ELEMENTS[match[2]] if match[2] else '')}
    match=re.fullmatch(r'选择自己牌库中任意卡牌最多([1-9]\d*)张，加入手牌。并重洗牌库。',t)
    if match:return {'kind':'search_hand','count':int(match[1]),'filter':'any','reveal':False}
    match=re.fullmatch(r'选择自己弃牌区中最多([1-9]\d*)张「基本([草火水雷超斗恶钢])能量」，在给对手看过之后，放回牌库并重洗牌库。',t)
    if match:return {'kind':'recover_deck','count':int(match[1]),'filter':'basic_energy:'+ELEMENTS[match[2]],'optional':True}
    match=re.fullmatch(r'选择自己(牌库|弃牌区)中最多([1-9]\d*)张「基本([草火水雷超斗恶钢])能量」，以任意方式附着于备战宝可梦身上。(并重洗牌库。)?',t)
    if match and bool(match[4])==(match[1]=='牌库'):
        return {'kind':'attach_multiple','origin':'left' if match[1]=='牌库' else 'discard','basic':True,'count':int(match[2]),'type':ELEMENTS[match[3]],'target':'bench'}
    match=re.fullmatch(r'选择自己手牌中任意数量的「基本([草火水雷超斗恶钢])能量」，以任意方式附着于自己的宝可梦身上。',t)
    if match:return {'kind':'attach_multiple','origin':'hand','count':'any','type':ELEMENTS[match[1]],'basic':True}
    match=re.fullmatch(r'若希望，可以?从牌库上方抽取卡牌，直到自己的手牌变为([1-9]\d*)张为止。',t)
    if match:return {'kind':'draw_until','count':int(match[1]),'optional':True}
    match=re.fullmatch(r'对手选择对手自己的([1-9]\d*)张手牌，放于弃牌区。',t)
    if match:return {'kind':'field_operation','operation':'hand_discard','opponent':True,'count':int(match[1])}
    match=re.fullmatch(r'若希望，可以?选择对手战斗宝可梦身上附着的([1-9]\d*)个能量，放回对手的手牌。',t)
    if match:return {'kind':'field_operation','operation':'return_opponent_energy','count':int(match[1]),'optional':True}
    match=re.fullmatch(r'抛掷1次硬币，?如果为正面，则选择对手的1只备战宝可梦，将其与战斗宝可梦互换。',t)
    if match:return {'kind':'gust','coin':True}
    if t=='抛掷1次硬币如果为正面，则在下一个对手的回合，这只宝可梦不会受到招式的伤害。':
        return {'kind':'attack_protection','coin':True,'damage':True}
    if t=='抛掷1次硬币如果为正面，则在下一个对手的回合，受到这个招式影响的宝可梦，无法使用招式。':
        return {'kind':'coin_branch','flips':1,'heads':[{'kind':'opponent_attack_lock'}]}
    expressions={
        '对手战斗宝可梦身上附着的能量数量':'opponent_energy',
        '自己备战宝可梦数量':'own_bench',
        '对手战斗宝可梦撤退所需能量数量':'opponent_retreat',
    }
    for phrase,term in expressions.items():
        match=re.fullmatch(r'(追加)?造成'+phrase+r'×([1-9]\d*)伤害。',t)
        if match:return {'kind':'damage_expression','term':term,'mode':'add' if match[1] else 'multiply','factor':int(match[2])}
    conditions={
        '如果对手的战斗宝可梦身上放置有伤害指示物的话，则':'opponent_damaged',
        '如果对手的战斗宝可梦为进化宝可梦的话，则':'opponent_evolved',
        '如果自己的手牌张数与对手的手牌张数相同的话，则':'equal_hands',
        '在这个回合，如果从手牌使出了支援者的话，则':'played_supporter',
    }
    for phrase,term in conditions.items():
        match=re.fullmatch(re.escape(phrase)+r'追加造成([1-9]\d*)伤害。',t)
        if match:return {'kind':'damage_expression','term':term,'mode':'add','factor':int(match[1])}
    match=re.fullmatch(r'如果对手的战斗宝可梦为1阶进化宝可梦的话，则追加造成([1-9]\d*)伤害。',t)
    if match:return {'kind':'damage_expression','term':'target_stage','stage':'STAGE_1','factor':int(match[1]),'mode':'add'}
    return None
