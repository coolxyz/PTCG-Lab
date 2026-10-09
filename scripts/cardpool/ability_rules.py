"""Complete bilingual ability clauses and explicit per-card usage contracts."""

import re
import json
from pathlib import Path

ADDITIONAL = json.loads(Path(__file__).with_name("ability-additional-clauses.json").read_text(encoding="utf8"))


def compile_ability(p):
    allowed = {
        "powertype",
        "type",
        "name",
        "ZHSname",
        "ZHTname",
        "ename",
        "jname",
        "effectZHS",
        "effectZHT",
        "ceffect",
        "eeffect",
        "jeffect",
        "link",
        "ZHAlt",
    }
    if (
        any(v and k not in allowed for k, v in p.items())
        or p.get("powertype") != "SV特性"
        or not p.get("ename")
    ):
        return None
    en = p.get("eeffect", "").replace("''", "")
    # Older wiki templates use ceffect or only supply traditional Chinese.
    # The exact Chinese/English pair must still match a reviewed clause.
    from scripts.cardpool.source_text import chinese_effect
    cn = chinese_effect(p)
    from scripts.cardpool.continuous_rules import compile_continuous

    result = compile_continuous(p.get("eeffect", ""), cn)
    from scripts.cardpool.hp_rules import compile_hp

    result = compile_hp(p.get("eeffect", ""), cn) or result
    from scripts.cardpool.passive_rules import compile_passive

    result = compile_passive(p.get("eeffect", ""), cn) or result
    from scripts.cardpool.reactive_rules import compile_reactive

    result = compile_reactive(p.get("eeffect", ""), cn) or result
    from scripts.cardpool.suppression_rules import compile_suppression

    result = compile_suppression(p.get("eeffect", ""), cn) or result
    from scripts.cardpool.entry_rules import compile_entry

    result = compile_entry(p.get("eeffect", ""), cn) or result
    from scripts.cardpool.activated_rules import compile_activated

    result = compile_activated(p.get("eeffect", ""), cn) or result
    from scripts.cardpool.energy_ability_rules import compile_energy_ability

    result = compile_energy_ability(p.get("eeffect", ""), cn) or result
    result = next((dict(c["rule"]) for c in ADDITIONAL if c["english"] == p.get("eeffect") and c["chinese"] == cn), result)
    e = re.fullmatch(
        r"Once during your turn, you may (?:use this Ability\. )?heal ([1-9]\d*) damage from (1 of|each of) your Pokémon\.",
        en,
        re.IGNORECASE,
    )
    c = re.fullmatch(
        r"在自己的回合可以使用1次。回复自己1只宝可梦「([1-9]\d*)」HP。", cn
    )
    ca = re.fullmatch(
        r"在自己的回合可以使用1次。将自己所有宝可梦的HP，各回复「([1-9]\d*)」。", cn
    )
    if e and (
        (e[2] == "1 of" and c and e[1] == c[1])
        or (e[2] == "each of" and ca and e[1] == ca[1])
    ):
        result = {"kind": "heal", "amount": int(e[1]), "all": e[2] == "each of"}
    for english, chinese, category in [
        ("a Pokémon", "1张宝可梦", "pokemon"),
        ("a Stadium card", "1张竞技场", "stadium"),
        (
            "an Ethan's Adventure card",
            "1张「{{TCG|阿响的冒险}}」",
            "name:Ethan's Adventure",
        ),
    ]:
        if (
            en
            == f"Once during your turn, you may search your deck for {english}, reveal it, and put it into your hand. Then, shuffle your deck."
            and cn
            in (
                f"在自己的回合可以使用1次。选择自己牌库中的{chinese}，在给对手看过之后加入手牌。并重洗牌库。",
                f"在自己的回合可以使用1次。选择自己牌库中的{chinese}，在给对手看过之后，加入手牌。并重洗牌库。",
            )
        ):
            result = {"kind": "search_hand", "count": 1, "filter": category}
    if (
        en
        == "Once during your turn, if this Pokémon is in the Active Spot, you may have your opponent reveal their hand."
        and cn
        == "如果这只宝可梦在战斗场上的话，则在自己的回合可以使用1次。查看对手的手牌。"
    ):
        result = {"kind": "reveal_opponent_hand", "activeOnly": True}
    if (
        en
        == "Once during your turn, you may use this Ability. Your Active Pokémon recovers from all Special Conditions."
        and cn == "在自己的回合可以使用1次。将自己战斗宝可梦的特殊状态，全部恢复。"
    ):
        result = {"kind": "recover_active_status"}
    if (
        en
        == "Once during your turn, if a Stadium is in play, you may make your opponent's Active Pokémon Poisoned."
        and cn
        == "如果场上有竞技场的话，则在自己的回合可以使用1次。令对手的战斗宝可梦陷入'''{{TCG|中毒}}'''状态。"
    ):
        result = {"kind": "poison_active", "stadiumRequired": True}
    e = re.fullmatch(
        r"Once during your turn, you may draw cards until you have ([1-9]\d*) cards in your hand\.",
        en,
    )
    c = re.fullmatch(
        r"在自己的回合可以使用1次。从牌库上方抽取卡牌，直到自己的手牌变为([1-9]\d*)张为止。",
        cn,
    )
    if e and c and e[1] == c[1]:
        result = {"kind": "draw_until", "count": int(e[1])}
    if (
        en
        == "Once during your turn, you may search your deck for a card and put it into your hand. Then, shuffle your deck. You can't use more than 1 Quick Search Ability each turn."
        and cn
        == "在自己的回合可以使用1次。选择自己牌库中任意1张卡牌，加入手牌。并重洗牌库。在这个回合，如果已经使用了其他的「音速搜索」的话，则无法使用这个特性。"
    ):
        result = {
            "kind": "search_any",
            "count": 1,
            "usageLimit": "once-per-player-per-turn",
            "sharedName": "Quick Search",
        }
    e = re.fullmatch(
        r"Once during your turn, if this Pokémon is in the Active Spot, you may heal ([1-9]\d*) damage from 1 of your Pokémon\.",
        en,
    )
    c = re.fullmatch(
        r"如果这只宝可梦在战斗场上的话，则在自己的回合可以使用1次。回复自己1只宝可梦「([1-9]\d*)」HP。",
        cn,
    )
    if e and c and e[1] == c[1]:
        result = {"kind": "heal", "amount": int(e[1]), "activeOnly": True}
    for english, chinese, details in [
        (
            "Prevent all effects of attacks used by your opponent's Pokémon done to this Pokémon. (Damage is not an effect.)",
            "这只宝可梦，不会受到对手宝可梦所使用招式的效果影响。",
            {"effects": True},
        ),
        (
            "As long as this Pokémon is on your Bench, prevent all damage from and effects of attacks from your opponent's Pokémon done to this Pokémon.",
            "只要这只宝可梦在备战区，就不会受到对手宝可梦的招式的伤害和效果影响。",
            {"damage": True, "effects": True, "zone": "bench"},
        ),
    ]:
        if en == english and cn == chinese:
            result = {
                "kind": "protection",
                "trigger": "passive",
                "usageLimit": "unlimited",
                **details,
            }
    if (
        p.get("eeffect")
        == "Prevent all damage done to this Pokémon from attacks by your opponent's Pokémon <big>'''''ex'''''</big> and Pokémon '''''V'''''."
        and cn == "这只宝可梦，不受到对手「宝可梦{{ex}}・{{V}}」的招式的伤害。"
    ):
        result = {
            "kind": "protection",
            "trigger": "passive",
            "usageLimit": "unlimited",
            "damage": True,
            "source": "ex_v",
        }
    e = re.fullmatch(
        r"This Pokémon takes ([1-9]\d*) less damage from attacks \(after applying Weakness and Resistance\)\.",
        en,
    )
    c = re.fullmatch(r"这只宝可梦所受到的招式的伤害「-([1-9]\d*)」。", cn)
    if e and c and e[1] == c[1]:
        result = {
            "kind": "armor",
            "amount": int(e[1]),
            "trigger": "passive",
            "usageLimit": "unlimited",
        }
    for english, chinese, n in [("a card", "1张卡牌", 1), ("2 cards", "2张卡牌", 2)]:
        if (
            en == f"Once during your turn, you may draw {english}."
            and cn == f"在自己的回合可以使用1次。从自己牌库上方抽取{chinese}。"
        ):
            result = {"kind": "draw", "count": n}
    if (
        en
        == "Once during your turn, you may switch your Active Pokémon with 1 of your Benched Pokémon."
        and cn == "在自己的回合可以使用1次。将自己的战斗宝可梦与备战宝可梦互换。"
    ):
        result = {"kind": "switch"}
    for cost, english in [(1, "a card"), (2, "2 cards")]:
        for draw, en_draw in [(1, "a card"), (2, "2 cards"), (3, "3 cards")]:
            e = f"You must discard {english} from your hand in order to use this Ability. Once during your turn, you may draw {en_draw}."
            variants = {
                f"在自己的回合，如果将自己的{cost}张手牌放于弃牌区的话，则可以使用1次。从自己牌库上方抽取{draw}张卡牌。",
                f"在自己的回合，如果将自己的{cost}张手牌放于弃牌区的话，则可使用1次。从自己牌库上方抽取{draw}张卡牌。",
            }
            if en == e and cn in variants:
                result = {"kind": "discard_draw", "cost": cost, "count": draw}
    if (
        en
        == "Once during your turn, you may shuffle your hand and put it on the bottom of your deck. If you put any cards on the bottom of your deck in this way, draw a card."
        and cn
        in {
            "在自己的回合可以使用1次。将自己所有的手牌翻到反面重洗，放回牌库下方。然后，从自己牌库上方抽取1张卡牌。",
            "在自己的回合可以使用1次。将自己所有的手牌翻到反面重洗，放回牌库下方。然后，从牌库上方抽取1张卡牌。",
        }
    ):
        result = {"kind": "bottom_hand_draw", "count": 1}
    if (
        en == "Once during your turn, you may have each player draw a card."
        and cn == "在自己的回合可以使用1次。双方玩家，各从牌库上方抽取1张卡牌。"
    ):
        result = {"kind": "both_draw", "count": 1}
    if (
        en
        == "Once during your turn, you may put 1 damage counter on this Pokémon. If you do, draw a card."
        and cn
        == "在自己的回合可以使用1次。给这只宝可梦身上放置1个伤害指示物。然后，从自己牌库上方抽取1张卡牌。"
    ):
        result = {"kind": "self_counter_draw", "count": 1}
    for eng, ch, element in [("Grass", "草", "GRASS"), ("Psychic", "超", "PSYCHIC")]:
        for target, en_target, cn_target in [
            ("self", "this Pokémon", "这只宝可梦"),
            ("bench", "1 of your Benched Pokémon", "备战宝可梦"),
        ]:
            for n, en_draw in [(1, "a card"), (2, "2 cards")]:
                if (
                    en
                    == f"Once during your turn, you may attach a Basic {{{{e|{eng}}}}} Energy card from your hand to {en_target}. If you attached Energy to a Pokémon in this way, draw {en_draw}."
                    and cn
                    == f"在自己的回合可以使用1次。选择自己手牌中的1张「基本{{{{e|{ch}}}}}能量」，附着于{cn_target}身上。然后，从自己牌库上方抽取{n}张卡牌。"
                ):
                    result = {
                        "kind": "attach_draw",
                        "type": element,
                        "target": target,
                        "count": n,
                    }
    if not result:
        from scripts.cardpool.cosmetic_rules import compile_cosmetic
        result = compile_cosmetic(p,"ability")
    if result:
        return {
            "name": p["ename"],
            "text": cn,
            "trigger": "activated",
            "usageLimit": "once-per-physical-pokemon-per-turn",
            "activeZones": ["active"]
            if result.get("activeOnly")
            else ["active", "bench"],
            **result,
        }
    return None
