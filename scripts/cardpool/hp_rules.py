"""Whole bilingual maximum-HP clauses; compound effects remain indivisible."""


def compile_hp(en, cn, *, tool=False):
    pairs = [
        (
            True,
            "The Basic Pokémon this card is attached to gets +50 HP.",
            "身上放有这张卡牌的'''基础'''宝可梦的最大HP「+50」。",
            {"hp": 50, "condition": "basic"},
        ),
        (
            True,
            "The Pokémon this card is attached to gets +100 HP.",
            "身上放有这张卡牌的宝可梦的最大HP「+100」。",
            {"hp": 100},
        ),
        (
            True,
            "The Cynthia's Pokémon this card is attached to gets +70 HP.",
            "身上放有这张卡牌的「竹兰的宝可梦」的最大HP「+70」。",
            {"hp": 70, "condition": "cynthia"},
        ),
        (
            False,
            "If this Pokémon has any {{e|Darkness}} Energy attached, it gets +100 HP, and the attacks it uses do 100 more damage to your opponent's Active Pokémon ''(before applying Weakness and Resistance)''.",
            "如果这只宝可梦身上附着了{{e|恶}}能量的话，则这只宝可梦的最大HP「+100」，这只宝可梦所使用的招式，给对手的战斗宝可梦造成的伤害「+100」。",
            {"scope": "self", "hp": 100, "damage": 100, "energy": "DARK"},
        ),
        (
            False,
            "If this Pokémon has 3 or more {{e|钢}} Energy attached, it gets +100 HP.",
            "如果这只宝可梦身上附着了3个及以上{{e|钢}}能量的话，则这只宝可梦的最大HP「+100」。",
            {"scope": "self", "hp": 100, "energy": "METAL", "energyMinimum": 3},
        ),
        (
            False,
            "This Pokémon gets +50 HP for each Prize card your opponent has taken.",
            "这只宝可梦的最大HP，会因为每张对手已经获得的奖赏卡而「+50」。",
            {"scope": "self", "hpPerOpponentPrize": 50},
        ),
        (
            False,
            "All of your Pokémon in play get +40 HP. The effect of Vibrant Dance doesn't stack.",
            "只要这只宝可梦在场上，自己场上所有宝可梦的最大HP各「+40」。无论拥有这个特性的宝可梦有多少只，这个效果都不会重复。",
            {"hp": 40, "noStack": "Vibrant Dance"},
        ),
    ]
    for is_tool, english, chinese, rule in pairs:
        if tool == is_tool and en == english and cn == chinese:
            return {
                "kind": "tool_modifier" if tool else "continuous",
                **({} if tool else {"trigger": "passive"}),
                **rule,
            }
    return None
