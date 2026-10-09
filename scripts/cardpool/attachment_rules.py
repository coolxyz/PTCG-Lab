"""Corroborated attachment attacks with origin and recipient contracts."""

from scripts.cardpool.extended_math_rules import ELEMENTS


def compile_attachment(p):
    en, cn = p.get("eeffect", ""), p.get("effectZHS", "")
    if p.get("damageP") or p.get("damage", "").endswith(("+", "×", "-")):
        return None
    if (
        en
        == "You may attach any number of Basic Energy cards from your hand to your Pokémon in any way you like."
        and cn == "选择自己手牌中任意数量的基本能量，以任意方式附着于自己的宝可梦身上。"
    ):
        return {
            "kind": "attach_multiple",
            "origin": "hand",
            "basic": True,
            "count": "any",
        }
    if (
        en
        == "Attach up to 2 Basic Energy cards from your discard pile to 1 of your Benched Pokémon."
        and cn == "选择自己弃牌区中最多2张基本能量，附着于1只备战宝可梦身上。"
    ):
        return {
            "kind": "attach_multiple",
            "origin": "discard",
            "basic": True,
            "count": 2,
            "target": "bench",
            "singleTarget": True,
        }
    for english, chinese, element in ELEMENTS:
        for alias in (english, chinese):
            if (
                en
                == f"Search your deck for a Basic {{{{e|{alias}}}}} Energy card and attach it to this Pokémon. Then, shuffle your deck."
                and cn
                == f"选择自己牌库中的1张「基本{{{{e|{chinese}}}}}能量」，附着于这只宝可梦身上。并重洗牌库。"
            ):
                return {
                    "kind": "attach_multiple",
                    "origin": "left",
                    "basic": True,
                    "type": element,
                    "count": 1,
                    "target": "self",
                }
            for count in range(1, 6):
                if (
                    en
                    == f"Attach up to {count} Basic {{{{e|{alias}}}}} Energy cards from your discard pile to your Benched Pokémon in any way you like."
                    and cn
                    == f"选择自己弃牌区中最多{count}张「基本{{{{e|{chinese}}}}}能量」，以任意方式附着于备战宝可梦身上。"
                ):
                    return {
                        "kind": "attach_multiple",
                        "origin": "discard",
                        "basic": True,
                        "type": element,
                        "count": count,
                        "target": "bench",
                    }
                if (
                    en
                    == f"Choose up to {count} of your Benched Pokémon. For each of those Pokémon, search your deck for a Basic {{{{e|{alias}}}}} Energy card and attach it to that Pokémon. Then, shuffle your deck."
                    and cn
                    == f"选择自己最多{count}只备战宝可梦，各附着1张牌库中的「基本{{{{e|{chinese}}}}}能量」。并重洗牌库。"
                ):
                    return {
                        "kind": "attach_multiple",
                        "origin": "left",
                        "basic": True,
                        "type": element,
                        "count": count,
                        "target": "bench",
                        "distinctTargets": True,
                    }
    return None
