"""Complete, corroborated non-HP passive clauses."""


def compile_continuous(en, cn):
    pairs = [
        (
            "Your Basic Pokémon's attacks do 30 more damage to your opponent's Active Pokémon (before applying Weakness and Resistance).",
            "只要这只宝可梦在场上，自己'''基础'''宝可梦所使用的招式，给对手战斗宝可梦造成的伤害「+30」。",
            {"damage": 30, "stage": "basic"},
        ),
        (
            "Attacks used by your Pokémon do 30 more damage to your opponent's Active Evolution Pokémon ''(before applying Weakness and Resistance)''.",
            "只要这只宝可梦在场上，自己宝可梦所使用的招式，给对手战斗场上的进化宝可梦造成的伤害「+30」。",
            {"damage": 30, "target": "evolved"},
        ),
        (
            "Attacks used by your Evolution {{e|Fire}} Pokémon do 10 more damage to your opponent's Active Pokémon ''(before applying Weakness and Resistance)''.",
            "只要这只宝可梦在场上，自己{{e|火}}属性的进化宝可梦所使用的招式，给对手战斗宝可梦造成的伤害「+10」。",
            {"damage": 10, "stage": "evolved", "types": ["FIRE"]},
        ),
        (
            "Attacks used by your {{e|Grass}} Pokémon and {{e|Fire}} Pokémon do 20 more damage to your opponent's Active Pokémon ''(before applying Weakness and Resistance)''.",
            "只要这只宝可梦在场上，自己的{{e|草}}或{{e|火}}宝可梦所使用的招式，给对手战斗宝可梦造成的伤害「+20」。",
            {"damage": 20, "types": ["GRASS", "FIRE"]},
        ),
        (
            "Attacks used by your Cynthia's Pokémon do 30 more damage to your opponent's Active Pokémon ''(before applying Weakness and Resistance)''.",
            "只要这只宝可梦在场上，自己「竹兰的宝可梦」所使用的招式给对手战斗宝可梦造成的伤害「+30」。",
            {"damage": 30, "prefix": "Cynthia's "},
        ),
        (
            "Attacks used by your Hop's Pokémon do 30 more damage to your opponent's Active Pokémon ''(before applying Weakness and Resistance)''. The effect of Extra Helpings doesn't stack.",
            "只要这只宝可梦在场上，自己「赫普的宝可梦」所使用的招式给对手战斗宝可梦造成的伤害「+30」。无论拥有这个特性的宝可梦有多少只，这个效果都不会重复。",
            {"damage": 30, "prefix": "Hop's ", "noStack": "Extra Helpings"},
        ),
        (
            "Attacks used by your Future Pokémon, except any Iron Crown ex, do 20 more damage to your opponent's Active Pokémon ''(before applying Weakness and Resistance).''",
            "只要这只宝可梦在场上，自己的「未来」宝可梦（除「铁头壳{{ex}}」外）所使用的招式，给对手战斗宝可梦造成的伤害「+20」。",
            {"damage": 20, "trait": "FUTURE", "exceptName": "Iron Crown ex"},
        ),
        (
            "As long as this Pokémon is on your Bench, attacks used by your Marowak do 30 more damage to your opponent's Active Pokémon ''(before applying Weakness and Resistance)''.",
            "只要这只宝可梦在备战区，自己的「嘎啦嘎啦」所使用的招式，给对手的战斗宝可梦造成的伤害「+30」。",
            {"damage": 30, "holderZone": "bench", "affectedName": "Marowak"},
        ),
        (
            "All of your Pokémon take 10 less damage from attacks from your opponent's Pokémon (after applying Weakness and Resistance).",
            "只要这只宝可梦在场上，自己所有的宝可梦，受到对手宝可梦的招式的伤害「-10」。",
            {"armor": 10},
        ),
        (
            "Your Basic Pokémon in play have no Retreat Cost.",
            "只要这只宝可梦在场上，自己所有'''基础'''宝可梦'''撤退'''所需能量，全部消除。",
            {"retreatFree": True, "stage": "basic"},
        ),
        (
            "All of your Pokémon that have {{e|超}} Energy attached have no Retreat Cost.",
            "只要这只宝可梦在场上，身上附着了{{e|超}}能量的自己所有宝可梦的'''撤退'''所需能量，全部消除。",
            {"retreatFree": True, "energy": "PSYCHIC"},
        ),
        (
            "All of your Pokémon that have {{e|Metal}} Energy attached have no Retreat Cost.",
            "只要这只宝可梦在场上，身上附着了{{e|钢}}能量的自己所有宝可梦'''撤退'''所需能量，全部消除。",
            {"retreatFree": True, "energy": "METAL"},
        ),
        (
            "If this Pokémon has any {{e|Psychic}} Energy attached, it has no Retreat Cost.",
            "如果这只宝可梦身上附着了{{e|超}}能量的话，则这只宝可梦'''撤退'''所需能量，全部消除。",
            {"retreatFree": True, "energy": "PSYCHIC", "scope": "self"},
        ),
        (
            "If this Pokémon has no Energy attached, it has no Retreat Cost.",
            "如果这只宝可梦身上没有附着任何能量的话，则这只宝可梦'''撤退'''所需能量，全部消除。",
            {"retreatFree": True, "noEnergy": True, "scope": "self"},
        ),
        (
            "If this Pokémon has no Energy attached, it has no Retreat Cost.",
            "如果这只宝可梦身上没有附着能量的话，则将这只宝可梦'''撤退'''所需能量全部消除。",
            {"retreatFree": True, "noEnergy": True, "scope": "self"},
        ),
        (
            "As long as this Pokémon is on your Bench, your Active Pokémon's Retreat Cost is {{e|Colorless}}{{e|Colorless}} less.",
            "只要这只宝可梦在备战区，自己的战斗宝可梦'''撤退'''所需能量减少2个。",
            {"retreatLess": 2, "holderZone": "bench", "affectedZone": "active"},
        ),
        (
            "Your opponent's Active Pokémon's Retreat Cost is {{e|无}} more.",
            "只要这只宝可梦在场上，对手战斗宝可梦'''撤退'''所需能量，就会增加1个。",
            {"retreatMore": 1, "scope": "opponent", "affectedZone": "active"},
        ),
        (
            "Your opponent's Active [[:Category:Evolution cards|Evolution Pokémon]]'s Retreat Cost is {{e|Colorless}} more.",
            "只要这只宝可梦在场上，对手战斗场上的进化宝可梦'''撤退'''所需能量，就会增加1个。",
            {
                "retreatMore": 1,
                "scope": "opponent",
                "affectedZone": "active",
                "stage": "evolved",
            },
        ),
    ]
    for english, chinese, rule in pairs:
        if en.replace("''", "") == english.replace("''", "") and cn == chinese:
            return {
                "kind": "continuous",
                "trigger": "passive",
                "usageLimit": "unlimited",
                **rule,
            }
    return None
