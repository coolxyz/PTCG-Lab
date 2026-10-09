"""Additional fully matched attack clauses sharing existing zone primitives."""

import re


def compile_supplement(p):
    en, cn = p.get("eeffect", ""), p.get("effectZHS", "")
    if p.get("damageP") or p.get("damage", "").endswith(("+", "×", "-")):
        return None
    for e, c, rule in [
        (
            "You may search your deck for a card and put it into your hand. Then, shuffle your deck.",
            "若希望，可选择自己牌库中任意1张卡牌，加入手牌。并重洗牌库。",
            {"kind": "search_hand", "count": 1, "filter": "any", "reveal": False},
        ),
        (
            "You may search your deck for up to 2 cards and put them into your hand. Then, shuffle your deck.",
            "若希望，可选择自己牌库中任意卡牌最多2张，加入手牌。并重洗牌库。",
            {"kind": "search_hand", "count": 2, "filter": "any", "reveal": False},
        ),
        (
            "If your opponent's Pokémon is Knocked Out by damage from this attack, take 1 more Prize card.",
            "如果因为这个招式的伤害，对手的宝可梦'''昏厥'''的话，则多拿取1张奖赏卡。",
            {"kind": "attack_extra_prize", "count": 1},
        ),
        (
            "You may draw cards until you have 6 cards in your hand.",
            "若希望，可从牌库上方抽取卡牌，直到自己的手牌数量变为6张为止。",
            {"kind": "draw_until", "count": 6, "optional": True},
        ),
        (
            "During your opponent's next turn, prevent all damage done to this Pokémon by attacks from Basic non-{{e|Colorless}} Pokémon.",
            "在下一个对手的回合，这只宝可梦不会受到'''基础'''宝可梦（除{{e|無}}宝可梦外）的招式的伤害。",
            {
                "kind": "attack_protection",
                "damage": True,
                "source": "basic",
                "exceptType": "COLORLESS",
            },
        ),
        (
            "Heal 30 damage from this Pokémon, and it recovers from all Special Conditions.",
            "回复这只宝可梦「30」HP，并恢复其所有特殊状态。",
            {
                "kind": "sequence",
                "steps": [
                    {"kind": "heal_self", "amount": 30},
                    {"kind": "recover_status"},
                ],
            },
        ),
        (
            "Discard 3 Energy from this Pokémon.",
            "选择这只宝可梦身上附着的3个能量，放于奔牌区。",
            {"kind": "discard_self_energy", "count": 3},
        ),
        (
            "This attack also does 30 damage to each of your Benched Pokémon. ''(Don't apply Weakness and Resistance for Benched Pokémon.)''",
            "给自己所有的备战宝可梦，也各造成30伤害。''[备战宝可梦不计算弱点、抗性。]''",
            {"kind": "own_bench_damage", "amount": 30},
        ),
        (
            "Both Active Pokémon are now Poisoned.",
            "令双方的战斗宝可梦，各陷入'''{{TCG|中毒}}'''状态。",
            {
                "kind": "special_status",
                "status": "POISONED",
                "target": "both",
                "coin": False,
            },
        ),
        (
            "Move all Energy from this Pokémon to your Benched Pokémon in any way you like.",
            "将这只宝可梦身上附着的所有能量，以任意方式转附于备战宝可梦身上。",
            {"kind": "move_self_energy", "all": True, "distribute": True},
        ),
        (
            "This Pokémon recovers from all Special Conditions.",
            "恢复这只宝可梦的全部特殊状态。",
            {"kind": "recover_status"},
        ),
        (
            "Discard the top card of your deck.",
            "将自己牌库上方1张卡牌放于弃牌区。",
            {"kind": "mill_self", "count": 1},
        ),
        (
            "During your opponent's next turn, they can't play any Item cards from their hand.",
            "在下一个对手的回合，对手无法从手牌使出物品。",
            {"kind": "item_lock"},
        ),
        (
            "Search your deck for a Basic Energy card and attach it to this Pokémon. Then, shuffle your deck.",
            "选择自己牌库中的1张基本能量，附着于这只宝可梦身上。并重洗牌库。",
            {"kind": "search_attach", "count": 1, "target": "self"},
        ),
    ]:
        if en == e and cn == c:
            return rule
    e = re.fullmatch(
        r"Switch in 1 of your opponent's Benched Pokémon to the Active Spot\. This attack does ([1-9]\d*) damage to the new Active Pokémon\.",
        en,
    )
    c = re.fullmatch(
        r"选择对手的1只备战宝可梦，将其与战斗宝可梦互换。然后，给新出场的宝可梦造成([1-9]\d*)伤害。",
        cn,
    )
    if e and c and e[1] == c[1] and not p.get("damage"):
        return {"kind": "gust_damage", "amount": int(e[1])}
    e = re.fullmatch(
        r"Put ([1-9]\d*) Energy attached to this Pokémon into your hand\.", en
    )
    c = re.fullmatch(r"选择这只宝可梦身上附着的([1-9]\d*)个能量，放回手牌。", cn)
    if e and c and e[1] == c[1]:
        return {"kind": "return_self_energy", "count": int(e[1])}
    for english, chinese, category in [
        ("Item", "物品", "item"),
        ("Supporter", "支援者", "supporter"),
    ]:
        if (
            en == f"Put an {english} card from your discard pile into your hand."
            or en == f"Put a {english} card from your discard pile into your hand."
        ):
            if cn == f"选择自己弃牌区中的1张{chinese}，在给对手看过之后，加入手牌。":
                return {"kind": "recover_hand", "count": 1, "filter": category}
    e = re.fullmatch(
        r"Put up to ([1-9]\d*) Pokémon from your discard pile into your hand\.", en
    )
    c = re.fullmatch(
        r"选择自己弃牌区中最多([1-9]\d*)张宝可梦，在给对手看过之后，加入手牌。", cn
    )
    if e and c and e[1] == c[1]:
        return {
            "kind": "recover_hand",
            "count": int(e[1]),
            "filter": "pokemon",
            "optional": True,
        }
    if (
        en
        == "Choose a random card from your opponent's hand. Your opponent reveals that card and shuffles it into their deck."
        and cn
        in (
            "在不看正面的前提下选择对手1张手牌，查看该卡牌的正面后，放回对手牌库并重洗牌库。",
            "在不看正面的前提下选择对手1张手牌，查看那张卡牌的正面后放回对手牌库并重洗牌库。",
        )
    ):
        return {"kind": "random_discard_hand", "destination": "deck"}
    e = re.fullmatch(
        r"This attack also does ([1-9]\d*) damage to each of your Benched Pokémon\. ''\(Don't apply Weakness and Resistance for Benched Pokémon\.\)''",
        en,
    )
    c = re.fullmatch(
        r"给自己所有备战宝可梦也各造成([1-9]\d*)伤害。''\[备战宝可梦不计算弱点、抗性。\]''",
        cn,
    )
    if e and c and e[1] == c[1]:
        return {"kind": "own_bench_damage", "amount": int(e[1])}
    for coin in (False, True):
        ep = "Flip a coin. If heads, your" if coin else "Your"
        cp = "抛掷1次硬币如果为正面，则" if coin else ""
        e = re.fullmatch(
            re.escape(ep)
            + r" opponent's Active Pokémon is now Poisoned\. During Pokémon Checkup, put ([1-9]\d*) damage counters on that Pokémon instead of 1\.",
            en,
        )
        c = re.fullmatch(
            re.escape(cp)
            + r"令对手的战斗宝可梦陷入'''{{TCG\|中毒}}'''状态。因这个'''中毒'''而放置的伤害指示物数量变为([1-9]\d*)个。",
            cn,
        )
        if e and c and e[1] == c[1]:
            return {
                "kind": "special_status",
                "status": "POISONED",
                "target": "opponent",
                "coin": coin,
                "poisonDamage": int(e[1]) * 10,
            }
    return None
