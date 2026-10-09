"""Closed bilingual attack expressions; unknown clauses remain blockers."""

import re


def compile_expression(p):
    from scripts.cardpool.extended_math_rules import compile_math

    expanded = compile_math(p)
    if expanded:
        return expanded
    en, cn = p.get("eeffect", ""), p.get("effectZHS", "")
    if cn == "这个招式的伤害不计算对手战斗宝可梦身上所附加的效果。":
        cn = "这个招式的伤害，不计算对手战斗宝可梦身上所附加的效果。"
    if not p.get("damageP"):
        if (
            en
            == "During your opponent's next turn, the Defending Pokémon can't attack."
            and cn == "在下一个对手的回合，受到这个招式影响的宝可梦，无法使用招式。"
        ):
            return {"kind": "opponent_attack_lock"}
        if (
            en
            == "Flip a coin. If tails, during your next turn, this Pokémon can't attack."
            and cn.replace("硬币，", "硬币")
            == "抛掷1次硬币如果为反面，则在下一个自己的回合，这只宝可梦无法使用招式。"
        ):
            return {"kind": "attack_lock", "coinTails": True}
        if (
            en
            == "Heal from this Pokémon the same amount of damage you did to your opponent's Active Pokémon."
            and cn == "回复这只宝可梦与给对手战斗宝可梦造成的伤害相同数值的HP。"
        ):
            return {"kind": "drain_damage"}
        states = {
            "Burned": ("灼伤", "BURNED"),
            "Poisoned": ("中毒", "POISONED"),
            "Asleep": ("睡眠", "ASLEEP"),
            "Confused": ("混乱", "CONFUSED"),
            "Paralyzed": ("麻痹", "PARALYZED"),
        }
        e = re.fullmatch(
            r"Your opponent's Active Pokémon is now (Burned|Poisoned|Asleep|Confused|Paralyzed) and (Burned|Poisoned|Asleep|Confused|Paralyzed)\.",
            en,
        )
        if e:
            first, second = states[e[1]], states[e[2]]
            c = (
                "令对手的战斗宝可梦陷入'''{{TCG|"
                + first[0]
                + "}}'''和'''{{TCG|"
                + second[0]
                + "}}'''状态。"
            )
            simple_cn = re.sub(
                r"{{TCG\|(灼伤|中毒|睡眠|混乱|麻痹)}}", r"\1", cn
            ).replace("'''", "")
            pair = re.fullmatch(
                r"令对手的战斗宝可梦陷入(灼伤|中毒|睡眠|混乱|麻痹)和(灼伤|中毒|睡眠|混乱|麻痹)状态。",
                simple_cn,
            )
            compatible = first[1] in ("POISONED", "BURNED") or second[1] in (
                "POISONED",
                "BURNED",
            )
            if cn == c or (
                pair and compatible and set(pair.groups()) == {first[0], second[0]}
            ):
                return {
                    "kind": "special_status",
                    "status": first[1],
                    "statuses": [first[1], second[1]],
                    "target": "opponent",
                    "coin": False,
                }
        if (
            en
            == "Before doing damage, discard all Pokémon Tools from your opponent's Active Pokémon."
            and cn
            == "在造成伤害前，将放于对手战斗宝可梦身上的「{{TCG|宝可梦道具}}」放于弃牌区。"
        ):
            return {"kind": "discard_tools_before_damage"}
    if not p.get("damageP"):
        if en in (
            "During your next turn, this Pokémon can't attack.",
            "During your next turn, this Pokémon can't use attacks.",
        ) and cn in (
            "在下一个自己的回合，这只宝可梦无法使用招式。",
            "在下一个自己的回合。这只宝可梦无法使用招式。",
        ):
            return {"kind": "attack_lock"}
        for duration, ep, cp in [
            (
                "next",
                r"During your next turn, this Pokémon can't use (.+)\.",
                r"在下一个自己的回合，这只宝可梦无法使用「(.+)」。",
            ),
            (
                "active",
                r"This Pokémon can't use (.+) again until it leaves the Active Spot\.",
                r"如果使用了这个招式的话，则这只宝可梦，在离开战斗场之前无法使用「(.+)」。",
            ),
        ]:
            e, c = re.fullmatch(ep, en), re.fullmatch(cp, cn)
            if e and c and e[1] == p.get("ename") and c[1] == p.get("ZHSname"):
                return {"kind": "named_attack_lock", "name": e[1], "duration": duration}
    prefix = "If you go second, you can't use this attack during your first turn. "
    cn_prefix = "这个招式，在后攻玩家的最初回合无法使用。"
    if en.startswith(prefix) and cn.startswith(cn_prefix):
        inner = compile_expression(
            {**p, "eeffect": en[len(prefix) :], "effectZHS": cn[len(cn_prefix) :]}
        )
        if inner and inner["kind"] == "damage_expression":
            return {**inner, "firstTurnForbidden": True}
    if not p.get("damageP"):
        e = re.fullmatch(r"Discard your hand and draw ([1-9]\d*) cards\.", en)
        c = re.fullmatch(
            r"将自己的手牌全部放于弃牌区，从牌库上方抽取([1-9]\d*)张卡牌。", cn
        )
        if e and c and e[1] == c[1]:
            return {"kind": "discard_draw", "count": int(e[1])}
        for zone, english, chinese in (
            ("bench", "Benched Pokémon", "备战宝可梦"),
            ("all", "Pokémon", "宝可梦"),
        ):
            for select in (True, False):
                e = re.fullmatch(
                    r"Put ([1-9]\d*) damage counters on "
                    + ("1" if select else "each")
                    + r" of your opponent's "
                    + english
                    + r"\.",
                    en,
                )
                c = re.fullmatch(
                    ("给对手的1只" if select else "给对手所有的")
                    + chinese
                    + r"身上，"
                    + ("" if select else "各")
                    + r"放置([1-9]\d*)个伤害指示物。",
                    cn,
                )
                if e and c and e[1] == c[1]:
                    return {
                        "kind": "place_counters",
                        "zone": zone,
                        "select": select,
                        "amount": int(e[1]) * 10,
                    }
            e = re.fullmatch(
                r"This attack does ([1-9]\d*) damage to each of your opponent's "
                + english
                + r"\. ''\(Don't apply Weakness and Resistance for Benched Pokémon\.\)''",
                en,
            )
            c = re.fullmatch(
                "给对手所有"
                + chinese
                + r"，?各造成([1-9]\d*)伤害。''\[备战宝可梦不计算弱点、抗性。\]''",
                cn,
            )
            if e and c and e[1] == c[1] and not p.get("damage"):
                return {"kind": "spread_damage", "zone": zone, "amount": int(e[1])}
        e = re.fullmatch(
            r"This attack also does ([1-9]\d*) damage to 1 of your opponent's Benched Pokémon\. ''\(Don't apply Weakness and Resistance for Benched Pokémon\.\)''",
            en,
        )
        c = re.fullmatch(
            r"给对手的1只备战宝可梦，?也造成([1-9]\d*)伤害。''\[备战宝可梦不计算弱点、抗性。\]''",
            cn,
        )
        if e and c and e[1] == c[1]:
            return {"kind": "bench_damage", "amount": int(e[1])}
        for target, english, chinese in (
            ("one", "1 of your Pokémon", "自己1只宝可梦"),
            ("all", "each of your Pokémon", ""),
        ):
            e = re.fullmatch(
                r"Heal ([1-9]\d*) damage from " + re.escape(english) + r"\.", en
            )
            c = re.fullmatch(
                ("回复" + chinese + r"「([1-9]\d*)」HP。")
                if target == "one"
                else r"将自己所有宝可梦的HP，各回复「([1-9]\d*)」。",
                cn,
            )
            if e and c and e[1] == c[1]:
                return {"kind": "heal_field", "amount": int(e[1]), "target": target}
        e = re.fullmatch(r"Discard the top ([1-9]\d*) cards of your deck\.", en)
        c = re.fullmatch(r"将自己牌库上方([1-9]\d*)张卡牌放于弃牌区。", cn)
        if e and c and e[1] == c[1]:
            return {"kind": "mill_self", "count": int(e[1])}
        if en == "Discard a Stadium in play." and cn == "将场上的竞技场放于弃牌区。":
            return {"kind": "discard_stadium"}
        if (
            en == "This Pokémon recovers from all Special Conditions."
            and cn == "将这只宝可梦的{{TCG|特殊状态}}，全部恢复。"
        ):
            return {"kind": "recover_status"}
        for coin in (True, False):
            if (
                en
                == ("Flip a coin. If heads, switch" if coin else "Switch")
                + " in 1 of your opponent's Benched Pokémon to the Active Spot."
                and cn
                == ("抛掷1次硬币如果为正面，则" if coin else "")
                + "选择对手的1只备战宝可梦，将其与战斗宝可梦互换。"
            ):
                return {"kind": "gust", "coin": coin}
    ignores = {
        (
            "This attack's damage isn't affected by Resistance.",
            "这个招式的伤害不计算抗性。",
        ): "resistance",
        (
            "This attack's damage isn't affected by any effects on your opponent's Active Pokémon.",
            "这个招式的伤害，不计算对手战斗宝可梦身上所附加的效果。",
        ): "effects",
        (
            "This attack's damage isn't affected by Weakness or Resistance, or by any effects on your opponent's Active Pokémon.",
            "这个招式的伤害，不计算弱点、抗性以及对手战斗宝可梦身上所附加的效果。",
        ): "all",
    }
    if (en, cn) in ignores and not p.get("damageP"):
        return {"kind": "ignore_damage_modifiers", "ignore": ignores[en, cn]}
    recoil_en = re.fullmatch(
        r"This Pokémon also does ([1-9]\d*) damage to itself\.", en
    )
    recoil_cn = re.fullmatch(r"给这只宝可梦也造成([1-9]\d*)伤害。", cn)
    if (
        recoil_en
        and recoil_cn
        and recoil_en[1] == recoil_cn[1]
        and not p.get("damageP")
    ):
        return {"kind": "recoil", "amount": int(recoil_en[1])}
    if not p.get("damageP"):
        cn_status = cn.replace(",", "，").replace(
            "抛掷1次硬币，如果", "抛掷1次硬币如果"
        )
        for english, chinese, status in (
            ("Confused", "混乱", "CONFUSED"),
            ("Asleep", "睡眠", "ASLEEP"),
            ("Paralyzed", "麻痹", "PARALYZED"),
            ("Poisoned", "中毒", "POISONED"),
            ("Burned", "灼伤", "BURNED"),
        ):
            for target in ("self", "opponent"):
                subject = (
                    "This Pokémon"
                    if target == "self"
                    else "Your opponent's Active Pokémon"
                )
                chinese_subject = (
                    "这只宝可梦" if target == "self" else "对手的战斗宝可梦"
                )
                for coin in (False, True):
                    expected_en = (
                        (
                            (
                                "Flip a coin. If heads, "
                                + subject[0].lower()
                                + subject[1:]
                            )
                            if coin
                            else subject
                        )
                        + " is now "
                        + english
                        + "."
                    )
                    for condition in (
                        "'''{{TCG|" + chinese + "}}'''",
                        "'''" + chinese + "'''",
                        "【" + chinese + "】",
                    ):
                        expected_cn = (
                            ("抛掷1次硬币如果为正面，则" if coin else "")
                            + "令"
                            + chinese_subject
                            + "陷入"
                            + condition
                            + "状态。"
                        )
                        if en == expected_en and cn_status == expected_cn:
                            return {
                                "kind": "special_status",
                                "status": status,
                                "target": target,
                                "coin": coin,
                            }
    if (
        en == "During your next turn, this Pokémon can't attack."
        and cn == "在下一个自己的回合，这只宝可梦无法使用招式。"
        and not p.get("damageP")
    ):
        return {"kind": "attack_lock"}
    terms = {
        "own_prizes": ("of your remaining Prize cards", "自己剩余奖赏卡张数"),
        "opponent_prizes": (
            "of your opponent's remaining Prize cards",
            "对手剩余奖赏卡张数",
        ),
        "own_hand": ("card in your hand", "自己手牌张数"),
        "opponent_hand": ("card in your opponent's hand", "对手手牌张数"),
        "own_energy": (
            "Energy attached to all of your Pokémon",
            "自己所有宝可梦身上附着的能量数量",
        ),
        "self_counters": (
            "damage counter on this Pokémon",
            "这只宝可梦身上放置的伤害指示物数量",
        ),
        "opponent_counters": (
            "damage counter on your opponent's Active Pokémon",
            "对手战斗宝可梦身上放置的伤害指示物数量",
        ),
        "opponent_energy": (
            "Energy attached to your opponent's Active Pokémon",
            "对手战斗宝可梦身上附着的能量数量",
        ),
        "active_energy": (
            "Energy attached to both Active Pokémon",
            "双方战斗宝可梦身上附着的能量数量",
        ),
        "self_energy": (
            "Energy attached to this Pokémon",
            "这只宝可梦身上附着的能量数量",
        ),
        "own_bench": ("of your Benched Pokémon", "自己备战宝可梦数量"),
        "opponent_bench": ("of your opponent's Benched Pokémon", "对手备战宝可梦数量"),
        "opponent_prizes_taken": (
            "Prize card your opponent has taken",
            "对手已经获得的奖赏卡张数",
        ),
        "own_prizes_taken": ("Prize card you have taken", "自己已经获得的奖赏卡张数"),
    }
    for term, (english, chinese) in terms.items():
        for mode in ("add", "multiply"):
            e = re.fullmatch(
                r"This attack does ([1-9]\d*) "
                + ("more " if mode == "add" else "")
                + r"damage for each "
                + re.escape(english)
                + r"\.",
                en,
            )
            c = re.fullmatch(
                ("追加" if mode == "add" else "")
                + "造成"
                + re.escape(chinese)
                + r"×([1-9]\d*)伤害。",
                cn,
            )
            modifier = "加" if mode == "add" else "乘"
            suffix = "+" if mode == "add" else "×"
            valid = p.get("damageP") == modifier or (
                not p.get("damageP") and p.get("damage", "").endswith(suffix)
            )
            if e and c and e[1] == c[1] and valid:
                if mode == "multiply" and p.get("damage", "").rstrip("×") != e[1]:
                    continue
                return {
                    "kind": "damage_expression",
                    "term": term,
                    "mode": mode,
                    "factor": int(e[1]),
                }
    e = re.fullmatch(
        r"This attack does ([1-9]\d*) less damage for each damage counter on this Pokémon\.",
        en,
    )
    c = re.fullmatch(
        r"这个招式的伤害，会被减少相当于这只宝可梦身上放置的伤害指示物数量×([1-9]\d*)(?:伤害)?的数值。",
        cn,
    )
    if (
        e
        and c
        and e[1] == c[1]
        and (p.get("damageP") == "减" or p.get("damage", "").endswith("-"))
    ):
        return {
            "kind": "damage_expression",
            "term": "self_counters",
            "mode": "subtract",
            "factor": int(e[1]),
        }
    conditions = {
        "opponent_ex_or_v": (
            "your opponent's Active Pokémon is a Pokémon ex or Pokémon V",
            "对手的战斗宝可梦是「宝可梦{{ex}}・{{V}}」",
        ),
        "opponent_evolved": (
            "your opponent's Active Pokémon is an Evolution Pokémon",
            "对手的战斗宝可梦为进化宝可梦",
        ),
        "opponent_basic": (
            "your opponent's Active Pokémon is a Basic Pokémon",
            "对手的战斗宝可梦为'''基础'''宝可梦",
        ),
        "opponent_ex": (
            "your opponent's Active Pokémon is a Pokémon ex",
            "对手的战斗宝可梦为「宝可梦{{ex}}」",
        ),
        "more_prizes": (
            "you have more Prize cards remaining than your opponent",
            "自己的剩余奖赏卡张数，比对手的剩余奖赏卡张数多",
        ),
        "equal_hands": (
            "you have the same number of cards in your hand as your opponent",
            "自己的手牌张数与对手的手牌张数相同",
        ),
        "opponent_damaged": (
            "your opponent's Active Pokémon already has any damage counters on it",
            "对手的战斗宝可梦身上放置有伤害指示物",
        ),
        "self_damaged": (
            "this Pokémon has any damage counters on it",
            "这只宝可梦身上放置有伤害指示物",
        ),
        "opponent_special": (
            "your opponent's Active Pokémon is affected by a Special Condition",
            "对手的战斗宝可梦处于特殊状态",
        ),
    }
    for term, (english, chinese) in conditions.items():
        e = re.fullmatch(
            "If " + re.escape(english) + r", this attack does ([1-9]\d*) more damage\.",
            en,
        )
        c = re.fullmatch(
            "如果" + re.escape(chinese) + r"的话，则追加造成([1-9]\d*)伤害。", cn
        )
        if (
            e
            and c
            and e[1] == c[1]
            and (p.get("damageP") == "加" or p.get("damage", "").endswith("+"))
        ):
            return {
                "kind": "damage_expression",
                "term": term,
                "mode": "add",
                "factor": int(e[1]),
            }
    for english, chinese, term in [
        (
            "Pokémon in your discard pile that has the United Wings attack",
            "自己弃牌区中，拥有招式「团结之翼」的宝可梦的张数",
            "united_wings",
        ),
        (
            "Pokémon Tool attached to all of your Pokémon",
            "自己所有宝可梦身上放有的「{{TCG|宝可梦道具}}」数量",
            "own_tools",
        ),
    ]:
        e = re.fullmatch(
            r"This attack does ([1-9]\d*) damage for each "
            + re.escape(english)
            + r"\.",
            en,
        )
        c = re.fullmatch("造成" + re.escape(chinese) + r"×([1-9]\d*)伤害。", cn)
        if (
            e
            and c
            and e[1] == c[1] == p.get("damage", "").rstrip("×")
            and (p.get("damageP") == "乘" or p.get("damage", "").endswith("×"))
        ):
            return {
                "kind": "damage_expression",
                "term": term,
                "factor": int(e[1]),
                "mode": "multiply",
            }
    for english, chinese, element in [
        ("Fire", "火", "FIRE"),
        ("Water", "水", "WATER"),
        ("Grass", "草", "GRASS"),
        ("Lightning", "雷", "LIGHTNING"),
        ("Psychic", "超", "PSYCHIC"),
        ("Fighting", "斗", "FIGHTING"),
        ("Darkness", "恶", "DARK"),
        ("Metal", "钢", "METAL"),
    ]:
        for mode in ("add", "multiply"):
            e = re.fullmatch(
                r"This attack does ([1-9]\d*) "
                + ("more " if mode == "add" else "")
                + r"damage for each "
                + re.escape("{{e|" + english + "}}")
                + r" Energy attached to this Pokémon\.",
                en,
            )
            c = re.fullmatch(
                ("追加" if mode == "add" else "")
                + "造成这只宝可梦身上附着的"
                + re.escape("{{e|" + chinese + "}}")
                + r"能量数量×([1-9]\d*)伤害。",
                cn,
            )
            valid = p.get("damageP") == ("加" if mode == "add" else "乘") or p.get(
                "damage", ""
            ).endswith("+" if mode == "add" else "×")
            if (
                e
                and c
                and e[1] == c[1]
                and valid
                and (mode == "add" or p.get("damage", "").rstrip("×") == e[1])
            ):
                return {
                    "kind": "damage_expression",
                    "term": "self_typed_energy",
                    "type": element,
                    "factor": int(e[1]),
                    "mode": mode,
                }
    return None
