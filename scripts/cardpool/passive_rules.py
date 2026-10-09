"""Full clauses for passive attack costs, constraints, and protection auras."""


def compile_passive(en, cn):
    pairs = [
        (
            "As long as this Pokémon is in the Active Spot, put 5 more damage counters on your opponent's Poisoned Pokémon during Pokémon Checkup.",
            "只要这只宝可梦在战斗场上，对手处于'''{{TCG|中毒}}'''状态的宝可梦，因'''中毒'''而放置的伤害指示物数量增加5个。",
            {"kind": "checkup", "activeOnly": True, "poisonBonus": 50},
        ),
        (
            "During Pokémon Checkup, put 3 more damage counters on your opponent's Burned Pokémon.",
            "只要这只宝可梦在场上，对手处于'''{{TCG|灼伤}}'''状态的宝可梦因'''{{TCG|灼伤}}'''而放置的伤害指示物数量增加3个。",
            {"kind": "checkup", "burnBonus": 30},
        ),
        (
            "During Pokémon Checkup, if this Pokémon is in the Active Spot, put 2 damage counters on each of your opponent's Basic Pokémon.",
            "只要这只宝可梦在战斗场上，每当宝可梦检查时，就给对手所有'''基础'''宝可梦身上各放置2个伤害指示物。",
            {"kind": "checkup", "activeOnly": True, "basicCounters": 20},
        ),
        (
            "Prevent all damage counters from being placed on your Benched Pokémon by effects of attacks used by your opponent's Basic Pokémon.",
            "只要这只宝可梦在场上，自己所有的备战宝可梦，不会因为对手的'''基础'''宝可梦所使用的招式的效果，而被放置伤害指示物。",
            {
                "kind": "protection",
                "counters": True,
                "scope": "team",
                "zone": "bench",
                "source": "basic",
            },
        ),
        (
            "Prevent all damage from and effects of attacks from your opponent's Tera Pokémon done to this Pokémon.",
            "这只宝可梦，不受到对手「{{TCG|太晶}}」宝可梦所使用招式的伤害和效果影响。",
            {"kind": "protection", "damage": True, "effects": True, "source": "tera"},
        ),
        (
            "As long as this Pokémon is on your Bench, prevent all damage from and effects of attacks from your opponent's Pokémon done to this Pokémon.",
            "只要这只宝可梦，处于备战区，就不会受到对手宝可梦的招式的伤害和效果影响。",
            {"kind": "protection", "damage": True, "effects": True, "zone": "bench"},
        ),
        (
            "Blood Moon used by this Pokémon costs {{e|Colorless}} less for each Prize card your opponent has taken.",
            "这只宝可梦使用「血月」所需能量会减少与对手已经获得的奖赏卡张数相同数量的{{e|无}}能量。",
            {
                "kind": "continuous",
                "scope": "self",
                "attackName": "Blood Moon",
                "attackLessPerOpponentPrize": 1,
            },
        ),
        (
            "Damage from attacks used by this Pokémon isn't affected by any effects on your opponent's Active Pokémon.",
            "这只宝可梦所使用的招式的伤害，不计算对手战斗宝可梦身上所附加的效果。",
            {"kind": "continuous", "scope": "self", "ignoreTargetEffects": True},
        ),
        (
            "Prevent all damage from attacks done to this Pokémon by your opponent's Pokémon that have an Ability.",
            "这只宝可梦，不受到对手拥有特性的宝可梦的招式的伤害。",
            {"kind": "protection", "damage": True, "source": "ability"},
        ),
        (
            "Prevent all effects of attacks used by your opponent's Pokémon done to all of your Pokémon that have Energy attached. (Existing effects are not removed. Damage is not an effect.)",
            "只要这只宝可梦在场上，身上附着了能量的自己所有的宝可梦，不会受到对手宝可梦所使用的招式的效果影响。（已经受到的效果，不会消失。）",
            {"kind": "protection", "effects": True, "scope": "team", "hasEnergy": True},
        ),
        (
            "As long as this Pokémon is in the Active Spot, attacks used by your opponent's Active Pokémon do 20 less damage ''(before applying Weakness and Resistance)''.",
            "只要这只宝可梦在战斗场上，对手战斗宝可梦使用的招式的伤害「-20」。",
            {
                "kind": "continuous",
                "scope": "opponent",
                "holderZone": "active",
                "affectedZone": "active",
                "damage": -20,
                "allTargets": True,
            },
        ),
        (
            "This Pokémon can't attack unless you have 4 or more Team Rocket's Pokémon in play.",
            "只有在自己场上的「火箭队的宝可梦」数量在4只及以上时，这只宝可梦才可以使用招式。",
            {
                "kind": "continuous",
                "scope": "self",
                "attackRequires": {"prefix": "Team Rocket's ", "count": 4},
            },
        ),
    ]
    for english, chinese, rule in pairs:
        if en == english and cn == chinese:
            return {"trigger": "passive", "usageLimit": "unlimited", **rule}
    return None
