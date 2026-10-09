"""Exact attachment and typed deck-search ability clauses."""

from scripts.cardpool.extended_math_rules import ELEMENTS


def compile_energy_ability(en, cn):
    if (
        en
        == "As often as you like during your turn, you may attach a Basic {{e|超}} Energy card from your discard pile to 1 of your {{e|超}} Pokémon. If you attached Energy to a Pokémon in this way, put 2 damage counters on that Pokémon. You can't use this Ability on a Pokémon that would be Knocked Out."
        and cn
        == "在自己的回合可以使用任意次。选择自己弃牌区中的1张「{{TCG|基本超能量|基本}}{{e|超}}{{TCG|基本超能量|能量}}」，附着于自己的{{e|超}}宝可梦身上。然后，在被附着的宝可梦身上放置2个伤害指示物。（对会被'''昏厥'''的宝可梦，无法使用这个特性。）"
    ):
        return {
            "kind": "attach_energy",
            "origin": "discard",
            "type": "PSYCHIC",
            "targetType": "PSYCHIC",
            "counterCost": 20,
            "usageLimit": "unlimited",
        }
    for english, chinese, element in ELEMENTS:
        for alias in (english, chinese):
            for unlimited in (True, False):
                ep = (
                    "As often as you like during your turn, you may "
                    if unlimited
                    else "Once during your turn, you may "
                )
                cp = (
                    "在自己的回合可以使用任意次。"
                    if unlimited
                    else "在自己的回合可以使用1次。"
                )
                for origin, eo, co in [
                    ("hand", "hand", "手牌"),
                    ("discard", "discard pile", "弃牌区"),
                ]:
                    for prefix, engtarget, cntarget in [
                        (None, "1 of your Pokémon", "自己的宝可梦"),
                        (
                            "Iono's ",
                            "1 of your Iono's Pokémon",
                            "自己的「奇树的宝可梦」",
                        ),
                    ]:
                        ee = (
                            ep
                            + f"attach a Basic {{{{e|{alias}}}}} Energy card from your {eo} to {engtarget}."
                        )
                        cc = (
                            cp
                            + f"选择自己{co}中的1张「基本{{{{e|{chinese}}}}}能量」，附着于{cntarget}身上。"
                        )
                        for heal in (0, 30):
                            es = ee + (
                                f" If you attached Energy to a Pokémon in this way, heal {heal} damage from that Pokémon."
                                if heal
                                else ""
                            )
                            cs = cc + (
                                f"然后，回复被附着能量的宝可梦「{heal}」HP。"
                                if heal
                                else ""
                            )
                            if en == es and cn == cs:
                                return {
                                    "kind": "attach_energy",
                                    "origin": origin,
                                    "type": element,
                                    "heal": heal,
                                    **({"prefix": prefix} if prefix else {}),
                                    "usageLimit": "unlimited"
                                    if unlimited
                                    else "once-per-physical-pokemon-per-turn",
                                }
            if (
                en
                == f"Once during your turn, you may search your deck for a Basic {{{{e|{alias}}}}} Energy card and attach it to this Pokémon. Then, shuffle your deck."
                and cn
                == f"在自己的回合可以使用1次。选择自己牌库中的1张「基本{{{{e|{chinese}}}}}能量」，附着于这只宝可梦身上。并重洗牌库。"
            ):
                return {
                    "kind": "search_attach",
                    "count": 1,
                    "type": element,
                    "target": "self",
                }
            for n in range(1, 4):
                if (
                    en
                    == f"Once during your turn, if this Pokémon is in the Active Spot, you may search your deck for up to {n} Basic {{{{e|{alias}}}}} Energy cards, reveal them, and put them into your hand. Then, shuffle your deck."
                    and cn
                    == f"如果这只宝可梦在战斗场上的话，则在自己的回合可以使用1次。选择自己牌库中最多{n}张「基本{{{{e|{chinese}}}}}能量」，在给对手看过之后，加入手牌。并重洗牌库。"
                ):
                    return {
                        "kind": "search_hand",
                        "count": n,
                        "filter": "basic_energy:" + element,
                        "activeOnly": True,
                    }
                if (
                    en
                    == f"Once during your turn, you may search your deck for up to {n} Basic {{{{e|{alias}}}}} Pokémon and put them onto your Bench. Then, shuffle your deck."
                    and cn
                    == f"在自己的回合可以使用1次。选择自己牌库中最多{n}张{{{{e|{chinese}}}}}属性的'''基础'''宝可梦，放于备战区。并重洗牌库。"
                ):
                    return {"kind": "search_bench", "count": n, "type": element}
    return None
