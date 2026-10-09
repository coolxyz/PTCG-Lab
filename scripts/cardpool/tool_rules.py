"""Closed bilingual rules for continuous Tool modifiers."""


def compile_tool(en, cn):
    import json
    from pathlib import Path
    for c in json.loads(Path(__file__).with_name("tool-additional-clauses.json").read_text(encoding="utf8")):
        if c["english"] == en and c["chinese"] == cn:
            return dict(c["rule"])
    from scripts.cardpool.hp_rules import compile_hp

    hp = compile_hp(en, cn, tool=True)
    if hp:
        return hp
    pairs = [
        (
            "The Future Pokémon this card is attached to has no Retreat Cost, and the attacks it uses do 20 more damage to your opponent's Active Pokémon (before applying Weakness and Resistance).",
            "身上放有这张卡牌的「未来」宝可梦，'''撤退'''所需能量全部消除，所使用的招式，给对手的战斗宝可梦造成的伤害「+20」。",
            {"condition": "future", "retreatFree": True, "damage": 20},
        ),
        (
            "If you have more Prize cards remaining than your opponent, the Pokémon this card is attached to takes 40 less damage from attacks from your opponent's Pokémon ''(after applying Weakness and Resistance)''.",
            "如果自己的剩余奖赏卡张数，比对手的剩余奖赏卡张数多的话，则身上放有这张卡牌的宝可梦，受到对手宝可梦的招式的伤害「-40」。",
            {"condition": "more_prizes", "armor": 40},
        ),
        (
            "If you have more Prize cards remaining than your opponent, the attacks of the Pokémon this card is attached to do 30 more damage to your opponent's Active Pokémon ''(before applying Weakness and Resistance).''",
            "如果自己的剩余奖赏卡张数，比对手的剩余奖赏卡张数多的话，则身上放有这张卡牌的宝可梦所使用的招式，给对手的战斗宝可梦造成的伤害「+30」。",
            {"condition": "more_prizes", "damage": 30},
        ),
        (
            "The attacks of the Pokémon this card is attached to do 30 more damage to your opponent's Active Pokémon '''''V''''' ''(before applying Weakness and Resistance)''.",
            "身上放有这张卡牌的宝可梦所使用的招式，给对手战斗场上的「宝可梦'''''V'''''」造成的伤害「+30」。",
            {"target": "v", "damage": 30},
        ),
        (
            "The attacks of the Pokémon this card is attached to do 10 more damage to your opponent's Active Pokémon (before applying Weakness and Resistance).",
            "身上放有这张卡牌的宝可梦所使用的招式，给对手的战斗宝可梦造成的伤害「+10」。",
            {"damage": 10},
        ),
        (
            "The Retreat Cost of the Pokémon this card is attached to is {{e|无}}{{e|无}} less.",
            "身上放有这张卡牌的宝可梦，'''{{TCG|撤退}}'''所需能量减少2个。",
            {"retreatLess": 2},
        ),
        (
            "The Stage 2 Pokémon this card is attached to has no Retreat Cost.",
            "身上放有这张卡牌的'''2阶进化'''宝可梦'''撤退'''所需能量，全部消除。",
            {"condition": "stage2", "retreatFree": True},
        ),
        (
            "Attacks used by the Pokémon this card is attached to do 50 more damage to your opponent's Active Pokémon ex ''(before applying Weakness and Resistance)''.",
            "身上放有这张卡牌的宝可梦所使用的招式，给对手战斗场上的「宝可梦{{ex}}」造成的伤害「+50」。",
            {"target": "ex", "damage": 50},
        ),
        (
            "Attacks used by the Hop's Pokémon this card is attached to cost {{e|Colorless}} less and do 30 more damage to your opponent's Active Pokémon ''(before applying Weakness and Resistance).''",
            "身上放有这张卡牌的「赫普的宝可梦」使用招式所需能量减少1个{{e|无}}能量，所使用的招式给对手战斗宝可梦造成的伤害「+30」。",
            {"condition": "hop", "attackLess": 1, "damage": 30},
        ),
    ]
    for english, chinese, rule in pairs:
        if en == english and cn == chinese:
            return {"kind": "tool_modifier", **rule}
    return None
