"""Bilingual counts and boolean predicates; no partial sentence matching."""

import re

ELEMENTS = [
    ("Grass", "草", "GRASS"),
    ("Fire", "火", "FIRE"),
    ("Water", "水", "WATER"),
    ("Lightning", "雷", "LIGHTNING"),
    ("Psychic", "超", "PSYCHIC"),
    ("Fighting", "斗", "FIGHTING"),
    ("Darkness", "恶", "DARK"),
    ("Metal", "钢", "METAL"),
    ("Colorless", "无", "COLORLESS"),
]


def compile_math(p):
    en, cn = p.get("eeffect", ""), p.get("effectZHS", "")
    mode = (
        "add"
        if p.get("damageP") == "加" or p.get("damage", "").endswith("+")
        else "multiply"
        if p.get("damageP") == "乘" or p.get("damage", "").endswith("×")
        else None
    )
    if mode is None:
        return None
    terms = [
        ("Benched Pokémon ''(both yours and your opponent's)''", "双方备战宝可梦数量", {"term": "both_benches"}),
        (
            "Basic Energy card in your opponent's discard pile",
            "对手弃牌区中{{TCG|基本能量卡|基本能量}}张数",
            {"term": "discard_energy", "owner": "opponent", "basic": True},
        ),
        (
            "Energy card in your opponent's discard pile",
            "对手弃牌区中的能量张数",
            {"term": "discard_energy", "owner": "opponent"},
        ),
        (
            "damage counter on all of your opponent's Pokémon",
            "对手所有宝可梦身上放置的伤害指示物数量",
            {"term": "opponent_all_counters"},
        ),
        (
            "Energy attached to all of your opponent's Pokémon",
            "对手所有宝可梦身上附着的能量数量",
            {"term": "opponent_all_energy"},
        ),
        (
            "of your Pokémon in play",
            "自己场上的宝可梦数量",
            {"term": "own_field_count"},
        ),
        (
            "of your Basic Pokémon in play",
            "自己场上'''基础'''宝可梦数量",
            {"term": "own_stage_count", "stage": "BASIC"},
        ),
        (
            "of your Stage 1 Pokémon in play",
            "自己场上'''1阶进化'''宝可梦数量",
            {"term": "own_stage_count", "stage": "STAGE_1"},
        ),
        (
            "of your opponent's Pokémon ex in play",
            "对手场上的「宝可梦{{ex}}」数量",
            {"term": "opponent_ex_count"},
        ),
        (
            "Special Energy card attached to this Pokémon",
            "这只宝可梦身上附着的特殊能量张数",
            {"term": "self_special_energy_cards"},
        ),
        (
            "{{TCG|Basic Energy card|Basic Energy}} attached to this Pokémon",
            "这只宝可梦身上附着的基本能量数量",
            {"term": "self_basic_energy_cards"},
        ),
        (
            "Special Condition affecting your opponent's Active Pokémon",
            "对手战斗宝可梦所处于的特殊状态数量",
            {"term": "opponent_status_count"},
        ),
        (
            "of your opponent's Pokémon in play that has an {{TCG|Ability}}",
            "对手场上拥有特性的宝可梦数量",
            {"term": "opponent_ability_count"},
        ),
        (
            "type of Basic Energy attached to all of your Pokémon",
            "自己所有宝可梦身上附着的基本能量的属性种类数量",
            {"term": "own_basic_energy_types"},
        ),
    ]
    for token in ("{{e}}", "{{e|无}}", "{{e|无色}}", "{{e|Colorless}}"):
        terms.append(
            (
                token + " in your opponent's Active Pokémon's Retreat Cost",
                "对手战斗宝可梦'''撤退'''所需能量数量",
                {"term": "opponent_retreat"},
            )
        )
    for english, chinese, name in [
        ("Maushold", "一家鼠", "Maushold"),
        ("Tatsugiri", "米立龙", "Tatsugiri"),
    ]:
        terms += [
            (
                f"of your {english} in play",
                f"自己场上的「{chinese}」数量",
                {"term": "own_named", "zone": "field", "name": name},
            ),
            (
                f"{english} in your discard pile",
                f"自己弃牌区中的「{chinese}」张数",
                {"term": "own_named", "zone": "discard", "name": name},
            ),
        ]
    for english, chinese, element in ELEMENTS:
        for alias in (english, chinese):
            terms += [
                (
                    f"{{{{e|{alias}}}}} Energy attached to all of your Pokémon",
                    f"自己所有宝可梦身上附着的{{{{e|{chinese}}}}}能量数量",
                    {"term": "own_typed_energy", "type": element},
                ),
                (
                    f"of your Benched Pokémon that has any {{{{e|{alias}}}}} Energy attached",
                    f"自己备战区中附着了{{{{e|{chinese}}}}}能量的宝可梦数量",
                    {"term": "bench_with_energy", "type": element},
                ),
                (
                    f"{{{{e|{alias}}}}} Pokémon in your discard pile",
                    f"自己弃牌区中{{{{e|{chinese}}}}}宝可梦的张数",
                    {"term": "typed_pokemon", "zone": "discard", "type": element},
                ),
                (
                    f"of your {{{{e|{alias}}}}} Pokémon in play",
                    f"自己场上{{{{e|{chinese}}}}}宝可梦数量",
                    {"term": "typed_pokemon", "zone": "field", "type": element},
                ),
                (
                    f"of your Benched {{{{e|{alias}}}}} Pokémon",
                    f"自己备战区中{{{{e|{chinese}}}}}宝可梦数量",
                    {"term": "typed_pokemon", "zone": "bench", "type": element},
                ),
                (
                    f"{{{{e|{alias}}}}} Energy attached to all of your opponent's Pokémon",
                    f"对手所有宝可梦身上附着的{{{{e|{chinese}}}}}能量数量",
                    {"term": "opponent_typed_energy", "type": element},
                ),
                (
                    f"{{{{e|{alias}}}}} Energy attached to this Pokémon",
                    f"这只宝可梦身上附着的{{{{e|{chinese}}}}}能量数量",
                    {"term": "self_typed_energy", "type": element},
                ),
            ]
    for english, chinese, term in terms:
        e = re.fullmatch(
            r"This attack does ([1-9]\d*) "
            + ("more " if mode == "add" else "")
            + "damage for each "
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
        if e and c and e[1] == c[1]:
            if mode == "multiply" and p.get("damage", "").rstrip("×") != e[1]:
                return None
            return {
                "kind": "damage_expression",
                "mode": mode,
                "factor": int(e[1]),
                **term,
            }
    if mode != "add":
        return None
    conditions = [
        (
            "your opponent's Active Pokémon is a Pokémon ex or Pokémon V",
            "对手的战斗宝可梦是「宝可梦'''''ex'''''・'''''V'''''」",
            {"term": "opponent_ex_or_v"},
        ),
        (
            "your opponent has 3 or fewer cards in their hand",
            "对手的手牌在3张及以下",
            {"term": "opponent_small_hand", "threshold": 3},
        ),
        (
            "this Pokémon has more Energy attached than your opponent's Active Pokémon",
            "这只宝可梦身上附着的能量数量比对手战斗宝可梦身上附着的能量数量多",
            {"term": "more_energy"},
        ),
        (
            "your opponent's Active Pokémon is a Pokémon ex",
            "对手的战斗宝可梦是「宝可梦{{ex}}」",
            {"term": "opponent_ex"},
        ),
        ("you have a Stadium in play", "场上有自己的竞技场", {"term": "own_stadium"}),
        ("a Stadium is in play", "场上有竞技场", {"term": "any_stadium"}),
        (
            "this Pokémon has any Special Energy attached",
            "这只宝可梦身上附着了特殊能量",
            {"term": "self_has_special_energy"},
        ),
        (
            "your opponent has exactly 2 or 4 Prize cards remaining",
            "对手的剩余奖赏卡张数为4张、2张",
            {"term": "opponent_prizes_2_4"},
        ),
        (
            "your opponent's Active Pokémon has a Pokémon Tool attached",
            "对手的战斗宝可梦身上放有「{{TCG|宝可梦道具}}」",
            {"term": "opponent_has_tool"},
        ),
        (
            "your opponent has 5 or fewer cards in their hand",
            "对手的手牌在5张及以下",
            {"term": "opponent_small_hand", "threshold": 5},
        ),
        (
            "there are 3 or fewer cards in your deck",
            "自己牌库的剩余张数在3张及以下",
            {"term": "own_small_deck", "threshold": 3},
        ),
    ]
    for eng, ch, status in [
        ("Poisoned", "中毒", "POISONED"),
        ("Confused", "混乱", "CONFUSED"),
        ("Asleep", "睡眠", "ASLEEP"),
        ("Burned", "灼伤", "BURNED"),
        ("Paralyzed", "麻痹", "PARALYZED"),
    ]:
        for subject, ce, owner in [
            ("this Pokémon", "这只宝可梦", "self"),
            ("your opponent's Active Pokémon", "对手的战斗宝可梦", "opponent"),
        ]:
            conditions.append(
                (
                    subject + " is " + eng,
                    ce + "处于'''{{TCG|" + ch + "}}'''状态",
                    {"term": "has_status", "owner": owner, "status": status},
                )
            )
    for eng, ch, element in ELEMENTS:
        for alias in (eng, ch):
            for glyph in ("{{e|" + ch + "}}", "【" + ch + "】"):
                conditions.append(
                    (
                        f"this Pokémon has any {{{{e|{alias}}}}} Energy attached",
                        "这只宝可梦身上附着了" + glyph + "能量",
                        {"term": "self_has_typed_energy", "type": element},
                    )
                )
    for eng, ch in [("Falinks", "列阵兵"), ("Mightyena", "大狼犬")]:
        for location in ("备战区", "备战区中"):
            conditions.append(
                (
                    eng + " is on your Bench",
                    "自己的" + location + "有「" + ch + "」",
                    {"term": "own_bench_named", "name": eng},
                )
            )
    for english, chinese, term in conditions:
        e = re.fullmatch(
            "If " + re.escape(english) + r", this attack does ([1-9]\d*) more damage\.",
            en,
        )
        c = re.fullmatch(
            "如果" + re.escape(chinese) + r"的话，则追加造成([1-9]\d*)伤害。", cn
        )
        if e and c and e[1] == c[1]:
            return {
                "kind": "damage_expression",
                "mode": "add",
                "factor": int(e[1]),
                **term,
            }
    return None
