"""Explicit bilingual protection rules with separate damage/effect semantics."""


def compile_protection(en, cn):
    cn = cn.replace("硬币，如果", "硬币如果")
    for english, chinese, rule in [
        (
            "Flip a coin. If heads, during your opponent's next turn, prevent all damage from and effects of attacks done to this Pokémon.",
            "抛掷1次硬币如果为正面，则在下一个对手的回合，这只宝可梦不受到招式的伤害和效果影响。",
            {"damage": True, "effects": True, "coin": True},
        ),
        (
            "Flip a coin. If heads, during your opponent's next turn, prevent all damage done to this Pokémon by attacks.",
            "抛掷1次硬币如果为正面，则在下一个对手的回合，这只宝可梦不受到招式的伤害。",
            {"damage": True, "coin": True},
        ),
        (
            "During your opponent's next turn, prevent all damage done to this Pokémon by attacks from Basic Pokémon.",
            "在下一个对手的回合，这只宝可梦不会受到'''基础'''宝可梦的招式的伤害。",
            {"damage": True, "source": "basic"},
        ),
        (
            "During your opponent's next turn, prevent all effects of attacks used by your opponent's Pokémon done to this Pokémon. ''(Damage is not an effect.)''",
            "在下一个对手的回合，这只宝可梦不受到对手宝可梦所使用招式的效果影响。",
            {"effects": True},
        ),
    ]:
        if en == english and cn.replace("不会受到", "不受到") == chinese.replace(
            "不会受到", "不受到"
        ):
            return {"kind": "attack_protection", **rule}
    return None
