"""Compile a deliberately small, closed rule grammar; reject all other text."""

import hashlib
import json
import re
from pathlib import Path
import sys
import mwparserfromhell as mw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.catalog.enrich import ELEMENTS, params, plain  # noqa: E402
from scripts.cardpool.catalog_inventory import load_articles, read  # noqa: E402
from packages.cardpool.scope import includes  # noqa: E402
from scripts.cardpool.attack_rules import compile_expression  # noqa: E402

# Template spelling aliases, not inferred changes to card rules.
ELEMENTS = {
    **ELEMENTS,
    "無色": "COLORLESS",
    "鋼": "METAL",
    "悪": "DARK",
    "闘": "FIGHTING",
    "一般": "COLORLESS",
}


def zone_rule(en, cn):
    """Closed bilingual grammar for reusable zone operations."""
    if (
        en == "You may discard a {{TCG|Stadium card|Stadium}} in play."
        and cn == "若希望，可将场上的竞技场放于弃牌区。"
    ):
        return {"kind": "discard_stadium", "optional": True}
    for english, chinese, kind in [
        (
            "Move an Energy from this Pokémon to 1 of your Benched Pokémon.",
            "选择这只宝可梦身上附着的1个能量，转附于备战宝可梦身上。",
            "move_self_energy",
        ),
        (
            "Put an Energy attached to this Pokémon into your hand.",
            "选择这只宝可梦身上附着的1个能量，放回手牌。",
            "return_self_energy",
        ),
        (
            "Your opponent reveals their hand.",
            "查看对手的手牌。",
            "reveal_opponent_hand",
        ),
        (
            "Discard a random card from your opponent's hand.",
            "在不看对手手牌正面的前提下，选择其中1张放于弃牌区。",
            "random_discard_hand",
        ),
    ]:
        if en == english and cn == chinese:
            return {"kind": kind}
    if (
        en == "Move all Energy from this Pokémon to 1 of your Benched Pokémon."
        and cn == "将这只宝可梦身上附着的所有能量，转附于1只备战宝可梦身上。"
    ):
        return {"kind": "move_self_energy", "all": True}
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
        for label in (english, chinese):
            e = re.fullmatch(
                r"Discard ([1-9]\d*) "
                + re.escape("{{e|" + label + "}}")
                + r" Energy from this Pokémon\.",
                en,
            )
            c = re.fullmatch(
                r"选择这只宝可梦身上附着的([1-9]\d*)个"
                + re.escape("{{e|" + chinese + "}}")
                + "能量，放于弃牌区。",
                cn,
            )
            if e and c and e[1] == c[1]:
                return {
                    "kind": "discard_self_energy",
                    "count": int(e[1]),
                    "type": element,
                }
    if (
        en
        == "Search your deck for up to 2 Basic Energy cards and attach them to your Pokémon in any way you like. Then, shuffle your deck."
        and cn
        == "选择自己牌库中最多2张基本能量，以任意方式附着于自己的宝可梦身上。并重洗牌库。"
    ):
        return {"kind": "search_attach", "count": 2, "target": "any"}
    for english, chinese in {
        "Fire": "火",
        "Water": "水",
        "Grass": "草",
        "Fighting": "斗",
        "Lightning": "雷",
        "Psychic": "超",
        "Darkness": "恶",
        "Metal": "钢",
    }.items():
        if (
            en
            == "Search your deck for up to 2 Basic {{e|"
            + english
            + "}} Energy cards and attach them to this Pokémon. Then, shuffle your deck."
            and cn
            == "选择自己牌库中最多2张「基本{{e|"
            + chinese
            + "}}能量」，附着于这只宝可梦身上。并重洗牌库。"
        ):
            return {
                "kind": "search_attach",
                "count": 2,
                "target": "self",
                "type": ELEMENTS[chinese],
            }
        if (
            en
            == "Search your deck for a Basic {{e|"
            + english
            + "}} Energy card and attach it to 1 of your Pokémon. Then, shuffle your deck."
            and cn
            == "选择自己牌库中1张「基本{{e|"
            + chinese
            + "}}能量」，附着于自己的宝可梦身上。并重洗牌库。"
        ):
            return {
                "kind": "search_attach",
                "count": 1,
                "target": "any",
                "type": ELEMENTS[chinese],
            }
    pairs = [
        (
            r"Draw cards until you have ([1-9]\d*) cards in your hand\.",
            r"从牌库上方抽取卡牌，直到自己的手牌变为([1-9]\d*)张为止。",
            "draw_until",
            "count",
        ),
        (
            r"Heal ([1-9]\d*) damage from this Pokémon\.",
            r"回复这只宝可梦「([1-9]\d*)」HP。",
            "heal_self",
            "amount",
        ),
        (
            r"Discard the top ([1-9]\d*) cards of your opponent's deck\.",
            r"将对手牌库上方的?([1-9]\d*)张卡牌放于弃牌区。",
            "mill_opponent",
            "count",
        ),
        (
            r"Discard ([1-9]\d*) Energy from this Pokémon\.",
            r"选择这只宝可梦身上附着的([1-9]\d*)个能量，放于弃牌区。",
            "discard_self_energy",
            "count",
        ),
    ]
    normalized = en.replace(
        "Discard an Energy from this Pokémon.", "Discard 1 Energy from this Pokémon."
    )
    normalized = normalized.replace(
        "Discard the top card of your opponent's deck.",
        "Discard the top 1 cards of your opponent's deck.",
    )
    for ep, cp, kind, parameter in pairs:
        e, c = re.fullmatch(ep, normalized), re.fullmatch(cp, cn)
        if e and c and e[1] == c[1]:
            return {"kind": kind, parameter: int(e[1])}
    if en == "Discard all Energy from this Pokémon." and cn in (
        "将这只宝可梦身上附着的能量，全部放于弃牌区。",
        "将这只宝可梦身上附着的能量全部放于弃牌区。",
    ):
        return {"kind": "discard_self_energy", "count": "all"}
    if (
        en == "Discard an Energy from your opponent's Active Pokémon."
        and cn == "选择对手战斗宝可梦身上附着的1个能量，放于弃牌区。"
    ):
        return {"kind": "discard_opponent_energy", "count": 1}
    for optional in (False, True):
        if (
            en
            == ("You may switch" if optional else "Switch")
            + " this Pokémon with 1 of your Benched Pokémon."
            and cn
            == ("若希望，可" if optional else "") + "将这只宝可梦与备战宝可梦互换。"
        ):
            return {"kind": "self_switch", "optional": optional}
    e = re.fullmatch(
        r"Search your deck for (a Basic Pokémon|up to ([1-9]\d*) Basic Pokémon) and put (it|them) onto your Bench\. Then, shuffle your deck\.",
        en,
    )
    c = re.fullmatch(
        r"选择自己牌库中(的1|最多([1-9]\d*))张'''基础'''宝可梦，放于备战区。并重洗牌库。",
        cn,
    )
    if (
        e
        and c
        and int(e[2] or 1) == int(c[2] or 1)
        and (bool(e[2]) == bool(c[2]))
        and e[3] == ("them" if e[2] else "it")
    ):
        return {"kind": "search_bench", "count": int(e[2] or 1)}
    filters = {
        "supporter": ("Supporter card", "Supporter cards", "支援者"),
        "pokemon": ("Pokémon", "Pokémon", "宝可梦"),
        "basic_energy": ("Basic Energy card", "Basic Energy cards", "基本能量"),
        "energy": ("Energy card", "Energy cards", "能量"),
        "tool": ("Pokémon Tool card", "Pokémon Tool cards", "「宝可梦道具」"),
        "stadium": ("Stadium card", "Stadium cards", "竞技场"),
    }
    for kind, (singular, plural, chinese) in filters.items():
        e = re.fullmatch(
            r"Search your deck for (?:a "
            + re.escape(singular)
            + r"|up to ([1-9]\d*) "
            + re.escape(plural)
            + r"), reveal (it|them), and put (it|them) into your hand\. Then, shuffle your deck\.",
            en,
        )
        c = re.fullmatch(
            r"选择自己牌库中(?:的?1|最多([1-9]\d*))张"
            + re.escape(chinese)
            + r"，在给对手看过之后，?加入手牌。并重洗牌库。",
            cn,
        )
        if (
            e
            and c
            and int(e[1] or 1) == int(c[1] or 1)
            and bool(e[1]) == bool(c[1])
            and e[2] == e[3] == ("them" if e[1] else "it")
        ):
            return {"kind": "search_hand", "filter": kind, "count": int(e[1] or 1)}
    return None


ZERO_DAMAGE_EFFECTS = {
    "advanced_attack",
    "attack_contract",
    "staged_attack",
    "field_operation",
    "coin_branch",
    "extended_field",
    "attach_multiple",
    "discard_damage",
    "item_lock",
    "gust_damage",
    "recover_hand",
    "recover_deck",
    "copy_attack",
    "attack_protection",
    "move_self_energy",
    "return_self_energy",
    "reveal_opponent_hand",
    "random_discard_hand",
    "coin_discard_energy",
    "prevent_retreat",
    "opponent_switch",
    "opponent_attack_lock",
    "target_damage",
    "sequence",
    "place_counters",
    "spread_damage",
    "discard_draw",
    "special_status",
    "heal_field",
    "recover_status",
    "gust",
    "mill_self",
    "discard_stadium",
    "search_attach",
    "draw",
    "damage_shield",
    "draw_until",
    "heal_self",
    "mill_opponent",
    "discard_self_energy",
    "discard_opponent_energy",
    "self_switch",
    "search_bench",
    "search_hand",
}


def coin_rule(p, allow_sequence=True):
    """Recognize complete bilingual sentences, never a substring of an effect."""
    from scripts.cardpool.advanced_attack_rules import compile_advanced
    advanced = compile_advanced(p)
    if advanced:
        return advanced
    from scripts.cardpool.staged_attack_rules import compile_staged
    staged = compile_staged(p)
    if staged:
        return staged
    from scripts.cardpool.attack_additional_rules import compile_additional
    additional = compile_additional(p)
    if additional:
        return additional
    from scripts.cardpool.coin_branch_rules import compile_branch
    branch = compile_branch(p)
    if branch:
        return branch
    from scripts.cardpool.conditional_attack_rules import compile_conditional
    conditional = compile_conditional(p)
    if conditional:
        return conditional
    p = {**p, "damage": re.sub(r"(?<=\d)x$", "×", p.get("damage", "")).replace("＋", "+")}
    if p.get("damageP") == "-":
        p["damageP"] = "减"
    from scripts.cardpool.math_clause_rules import compile_math_clause
    math_clause = compile_math_clause(p)
    if math_clause:
        return math_clause
    from scripts.cardpool.attack_field_rules import compile_field
    field = compile_field(p)
    if field:
        return field
    from scripts.cardpool.attachment_rules import compile_attachment

    attachment = compile_attachment(p)
    if attachment:
        return attachment
    p = {
        **p,
        "eeffect": p.get("eeffect", "").replace("’", "'"),
        "effectZHS": p.get("effectZHS", "")
        .replace("麻痺", "麻痹")
        .replace("抛掷1次硬币，如果", "抛掷1次硬币如果"),
    }
    p["effectZHS"] = re.sub(r"(?<=[数次])x(?=\d)", "×", p["effectZHS"])
    p["effectZHS"] = p["effectZHS"].replace("''[备战宝可梦不计算弱点，抗性。]''", "''[备战宝可梦不计算弱点、抗性。]''")
    p["eeffect"] = re.sub(r"(?:'')?\(Don't apply Weakness (?:and|or) Resistance for Benched Pokémon\.\)(?:'')?", "''(Don't apply Weakness and Resistance for Benched Pokémon.)''", p["eeffect"].replace(".'' (", ". ("))
    p["eeffect"] = re.sub(
        r"{{TCG\|(Paralyzed|Poisoned|Burned|Asleep|Confused)}}", r"\1", p["eeffect"]
    )
    from scripts.cardpool.supplemental_rules import compile_supplement
    from scripts.cardpool.discard_damage_rules import compile_discard_damage

    discard_damage = compile_discard_damage(p)
    if discard_damage:
        return discard_damage

    supplement = compile_supplement(p)
    if supplement:
        return supplement
    from scripts.cardpool.target_rules import compile_target

    targeted = compile_target(p)
    if targeted:
        return targeted
    en, cn = p.get("eeffect", ""), p.get("effectZHS", "")
    if (
        p.get("ename") == "Genome Hacking"
        and not p.get("damage")
        and not p.get("damageP")
        and en
        == "Choose 1 of your opponent's Active Pokémon's attacks and use it as this attack."
        and cn == "选择对手战斗宝可梦所拥有的1个招式，作为这个招式使用。"
    ):
        return {"kind": "copy_attack"}
    if not p.get("damageP"):
        from scripts.cardpool.protection_rules import compile_protection

        protection = compile_protection(en, cn)
        if protection:
            return protection
    expression = compile_expression(p)
    if expression:
        return expression
    cn = cn.replace(",", "，").replace("抛掷1次硬币，如果", "抛掷1次硬币如果")
    if not p.get("damageP"):
        rule = zone_rule(en, cn)
        if rule:
            return rule
    # Require corroboration for this exact source transcription typo.
    if (
        cn == "在下一个对手的固合，受到这个招式影响的宝可梦，无法撤退。"
        and p.get("effectZHT") == "在下個對手的回合，受到這個招式的寶可夢無法撤退。"
    ):
        cn = cn.replace("固合", "回合")
    se = re.fullmatch(
        r"During your opponent's next turn, this Pokémon takes ([1-9]\d*) less damage from attacks \(after applying Weakness and Resistance\)\.",
        en.replace("''", ""),
    )
    sc = re.fullmatch(
        r"在下一个对手的回合，这只宝可梦所受到的招式的伤害「-([1-9]\d*)」。", cn
    )
    if se and sc and se[1] == sc[1] and not p.get("damageP"):
        return {"kind": "damage_shield", "amount": int(se[1])}
    de = re.fullmatch(r"Draw (a card|([1-9]\d*) cards)\.", en)
    dc = re.fullmatch(r"从自己牌库上方抽取([1-9]\d*)张卡牌。", cn)
    if de and dc and int(de[2] or 1) == int(dc[1]) and not p.get("damageP"):
        return {"kind": "draw", "count": int(dc[1])}
    ce = re.fullmatch(
        r"Flip ([1-9]\d*) coins\. This attack does ([1-9]\d*) damage for each heads\.",
        en,
    )
    cc = re.fullmatch(r"抛掷([1-9]\d*)次硬币，造成正面次数×([1-9]\d*)伤害。", cn)
    ue = re.fullmatch(
        r"Flip a coin until you get tails\. This attack does ([1-9]\d*) damage for each heads\.",
        en,
    )
    uc = re.fullmatch(r"抛掷硬币直到出现反面，造成正面次数×([1-9]\d*)伤害。", cn)
    multiplier = (p.get("damageP") == "乘" and p.get("damage", "").isdigit()) or (
        not p.get("damageP") and re.fullmatch(r"[1-9]\d*×", p.get("damage", ""))
    )
    value = p.get("damage", "").rstrip("×")
    if multiplier:
        from scripts.cardpool.extended_math_rules import ELEMENTS as MATH_ELEMENTS
        terms = [("Energy attached to this Pokémon", "这只宝可梦身上附着的能量数量", "self_energy", None), ("Energy attached to both Active Pokémon", "双方战斗宝可梦身上附着的能量数量", "active_energy", None)]
        for english, chinese, element in MATH_ELEMENTS:
            for alias in (english, chinese):
                terms.append((f"{{{{e|{alias}}}}} Energy attached to this Pokémon", f"这只宝可梦身上附着的{{{{e|{chinese}}}}}能量数量", "self_typed_energy", element))
        for english, chinese, term, element in terms:
            e = re.fullmatch("Flip a coin for each " + re.escape(english) + r"\. This attack does ([1-9]\d*) damage for each heads\.", en)
            c = re.fullmatch("抛掷与" + re.escape(chinese) + r"相同次数的硬币，造成正面次数×([1-9]\d*)点?伤害。", cn)
            if e and c and e[1] == c[1] == value:
                return {"kind": "coin_count", "countTerm": term, "perHead": int(value), **({"type": element} if element else {})}
    if multiplier and ce and cc and ce.groups() == cc.groups() and ce[2] == value:
        return {"kind": "coin_count", "count": int(ce[1]), "perHead": int(value)}
    if multiplier and ue and uc and ue[1] == uc[1] == value:
        return {"kind": "coin_until_tails", "perHead": int(value)}
    e = re.fullmatch(
        r"Flip a coin until you get tails\. This attack does ([1-9]\d*) more damage for each heads\.",
        en,
    )
    c = re.fullmatch(r"抛掷硬币直到出现反面，追加造成正面次数×([1-9]\d*)伤害。", cn)
    if (
        e
        and c
        and e[1] == c[1]
        and (p.get("damageP") == "加" or p.get("damage", "").endswith("+"))
    ):
        return {"kind": "coin_until_tails", "perHead": int(e[1]), "mode": "add"}
    if not p.get("damageP"):
        if (
            en
            in (
                "Switch out your opponent's Active Pokémon to the Bench. ''(Your opponent chooses the new Active Pokémon.)''",
                "Switch out your opponent's Active Pokémon to the Bench. (Your opponent chooses the new Active Pokémon.)",
            )
            and cn
            == "将对手的战斗宝可梦与备战宝可梦互换。''[放于战斗场的宝可梦由对手选择。]''"
        ):
            return {"kind": "opponent_switch"}
        if (
            en == "Discard a {{e|水}} Energy from your opponent's Active Pokémon."
            and cn == "选择对手战斗宝可梦身上附着的1个{{e|水}}能量，放于弃牌区。"
        ):
            return {"kind": "discard_typed_energy", "type": "WATER"}
    if not p.get("damageP"):
        if (
            en
            == "During your opponent's next turn, the Defending Pokémon can't retreat."
            and cn
            in (
                "在下一个对手的回合，受到这个招式影响的宝可梦，无法撤退。",
                "在下一个对手的回合，受到这个招式影响的宝可梦无法撤退。",
            )
        ):
            return {"kind": "prevent_retreat"}
        if (
            en
            == "Flip a coin. If heads, discard an Energy from your opponent's Active Pokémon."
            and cn
            == "抛掷1次硬币如果为正面，则选择对手战斗宝可梦身上附着的1个能量，放于弃牌区。"
        ):
            return {"kind": "coin_discard_energy"}
    if (
        en == "Draw a card."
        and cn == "从自己牌库上方抽取1张卡牌。"
        and not p.get("damageP")
        and not p.get("damage")
    ):
        return {"kind": "draw", "count": 1}
    if (
        en == "Flip a coin. If tails, this attack does nothing."
        and cn == "抛掷1次硬币如果为反面，则这个招式失败。"
        and not p.get("damageP")
    ):
        return {"kind": "coin_fail"}
    e = re.fullmatch(
        r"Flip a coin\. If heads, this attack does (\d+) more damage\.", en
    )
    c = re.fullmatch(r"抛掷1次硬币如果为正面，则追加造成(\d+)伤害。", cn)
    plus = (p.get("damageP") == "加" and p.get("damage", "").isdigit()) or (
        not p.get("damageP") and re.fullmatch(r"\d+\+", p.get("damage", ""))
    )
    if e and c and e[1] == c[1] and plus:
        return {"kind": "coin_bonus", "bonus": int(e[1])}
    if allow_sequence:
        from scripts.cardpool.attack_sequence import compile_sequence
        sequence = compile_sequence(p, lambda part: coin_rule(part, False))
        if sequence:
            return sequence
    from scripts.cardpool.cosmetic_rules import compile_cosmetic
    return compile_cosmetic(p)


def compile_rule(page, english_names=None):
    text = re.sub(r"<!--.*?-->","",page.get("text") or "",flags=re.S)
    # The boundary heading is layout, not a card rule. A single explicit
    # header also delimits older articles that omitted that heading.
    if text and "==卡牌信息==" not in text and text.count("{{卡牌信息/header") == 1:
        text = text.replace("{{卡牌信息/header", "==卡牌信息==\n{{卡牌信息/header", 1)
    if not text or "==卡牌信息==" not in text:
        return None
    body = text.split("==卡牌信息==", 1)[1].split("{{ExpansionList", 1)[0]
    templates = mw.parse(body).filter_templates(recursive=False)
    if str(mw.parse(body).strip_code()).replace("\u200b", "").strip():
        return None
    names = [str(t.name).strip() for t in templates]
    # Both footer templates encode the same explicit weakness/resistance fields.
    for i, name in enumerate(names):
        if name == "卡牌信息/ssend":
            names[i] = "卡牌信息/svend"
    headers = {"卡牌信息/header", "卡牌信息/header/太晶", "卡牌信息/header/KM"}
    if set(names) - headers - {"卡牌信息/attack", "卡牌信息/svend", "卡牌信息/power"}:
        return None
    from scripts.cardpool.ability_rules import compile_ability

    powers = [
        compile_ability(params(t))
        for t in templates
        if str(t.name).strip() == "卡牌信息/power"
    ]
    if len(powers) > 1 or any(p is None for p in powers):
        return None
    found_headers = [n for n in names if n in headers]
    if len(found_headers) != 1 or names.count("卡牌信息/svend") != 1:
        return None
    h = params(templates[names.index(found_headers[0])])
    from scripts.cardpool.source_corrections import apply as repair_source
    corrections = repair_source(page, "header", h)
    rule = "TERA" if found_headers[0].endswith("/太晶") else "NONE"
    if found_headers[0].endswith("/KM"):
        rule = {"古代": "ANCIENT", "未来": "FUTURE", "未來": "FUTURE"}.get(
            h.get("class")
        )
        if not rule:
            return None
        h["class"] = h.pop("class3", "")
    if not h.get("evostage") and re.search(
        r"\{\{TCG\|(?:基础|基礎)宝可梦\}\}", text.split("==卡牌信息==", 1)[0]
    ):
        h["evostage"] = "基础"
    end = params(templates[names.index("卡牌信息/svend")])
    corrections += repair_source(page, "end", end)
    if {k for k, v in end.items() if v} - {
        "type",
        "weakness",
        "resistance",
        "retreat",
        "jdex",
        "edex",
        "dex",
        "ZHSdex",
        "ZHTdex",
        "ZHAlt",
        "disableEN",
        "disableJA",
        "class",
    }:
        return None
    stages = {
        "基础": "BASIC",
        "基礎": "BASIC",
        "1阶进化": "STAGE_1",
        "2阶进化": "STAGE_2",
        "1階進化": "STAGE_1",
        "2階進化": "STAGE_2",
    }
    if h.get("evostage") not in stages or not h.get("hp", "").isdigit():
        return None
    if {k for k, v in h.items() if v} - {
        "name",
        "hp",
        "type",
        "evostage",
        "image",
        "ndex",
        "species",
        "height",
        "weight",
        "evo",
        "evonumber",
        "class",
        "的宝可梦",
        "form",
        "names",
        "nodex",
        "pic",
        "class3",
    }:
        return None
    stage = stages[h["evostage"]]
    end_class = end.get("class", "")
    if end_class == "ext" and rule == "TERA":
        end_class = "ex"
    if h.get("class") == "太晶" and h.get("class3") == "ex":
        rule, h["class"] = "TERA", "ex"
    if end_class == "ext":
        end_class, rule = "ex", "TERA"
    if end_class == "太晶" and h.get("class") == "ex" and rule == "TERA":
        end_class = "ex"
    if end_class in ("古代","未来","未來") and rule in ("ANCIENT","FUTURE"):
        end_class = ""
    if end_class == "Pokémon":
        end_class = ""
    if end_class == "ex" and not h.get("class"):
        h["class"] = "ex"
    if h.get("class", "") not in ("", "ex") or end_class != h.get("class", ""):
        return None
    from scripts.cardpool.source_names import evolution
    previous = evolution(h, english_names or {})
    if stage != "BASIC" and len(previous) != 1:
        return None
    if h.get("type") not in ELEMENTS or not end.get("retreat", "").isdigit():
        return None
    if any(
        k in end and end[k]
        for k in ("rule", "weaknessM", "resistanceM", "weaknessP", "resistanceP")
    ):
        return None
    for key in ("weakness", "resistance"):
        if end.get(key) and end[key] not in ELEMENTS:
            return None
    intro = mw.parse(text.split("==卡牌信息==", 1)[0]).filter_templates()
    identity = next((params(t) for t in intro if str(t.name).strip() == "N"), {})
    corrections += repair_source(page, "identity", identity)
    english = plain(identity.get("4", ""))
    from scripts.cardpool.source_names import BY_NAME, BY_NUMBER
    canonical = BY_NAME.get(plain(h.get("name","")))
    title_base = page.get("title","").split("（",1)[0]
    if canonical and title_base in (h.get("name"),h.get("name","")+"ex") and not h.get("form") and not h.get("names") and not h.get("的宝可梦") and h.get("ndex","").isdigit() and BY_NUMBER.get(int(h["ndex"])) == canonical:
        english = canonical + (" ex" if h.get("class") == "ex" else "")
    if (
        not english
        or bool(re.search(r"\bex\b", english)) != (h.get("class") == "ex")
        or "V" in english.split()
    ):
        return None
    if h.get("的宝可梦") and stage != "BASIC":
        if "'s " not in english:
            return None
        prefix = english.split("'s ", 1)[0] + "'s "
        previous = {n if n.startswith(prefix) else prefix + n for n in previous}
    if stage != "BASIC" and english in previous:
        return None
    attacks = []
    for template in templates:
        if str(template.name).strip() != "卡牌信息/attack":
            continue
        p = params(template)
        corrections += repair_source(page, "attack:" + p.get("ename", ""), p)
        from scripts.cardpool.source_text import chinese_effect
        if not p.get("effectZHS") and chinese_effect(p):
            p["effectZHS"] = chinese_effect(p)
        if not p.get("ename") and end.get("disableEN") == "1":
            p["ename"] = p.get("ZHSname") or p.get("name")
        mechanic = coin_rule(p)
        allowed = {
            "type",
            "ZHSname",
            "ZHTname",
            "name",
            "jname",
            "ename",
            "cost",
            "damage",
            "link",
        }
        if mechanic:
            allowed |= {"ceffect", "effectZHS", "effectZHT", "eeffect", "jeffect", "damageP"}
        # Unknown or nonempty effect fields, plus/multiplier damage, and other
        # mechanics are blockers, never reduced to a vanilla attack.
        if any(k not in allowed and not k.isdigit() and v for k, v in p.items()):
            return None
        costs = [p.get("cost", "")] + [
            str(x.value).strip() for x in template.params if not x.showkey
        ]
        if any(c not in ELEMENTS and c not in ("", "0") for c in costs):
            return None
        damage = p.get("damage") or (
            "0" if mechanic and mechanic["kind"] in ZERO_DAMAGE_EFFECTS else ""
        )
        damage = damage.replace("＋", "+").replace("−","-")
        if mechanic and mechanic["kind"] == "advanced_attack":
            damage = "0"
        if mechanic and mechanic["kind"] == "staged_attack":
            damage = "0" if mechanic.get("mode") == "multiply" else damage.rstrip("+")
        if mechanic and mechanic["kind"] == "coin_branch":
            damage = "0" if mechanic.get("perHead") else damage.rstrip("+")
        if mechanic and mechanic["kind"] in ("coin_count", "coin_until_tails"):
            damage = (
                damage.rstrip("+")
                if mechanic.get("mode") == "add"
                else str(mechanic["perHead"])
            )
        if mechanic and mechanic["kind"] == "coin_bonus":
            damage = damage.rstrip("+")
        if mechanic and mechanic["kind"] == "damage_expression":
            damage = "0" if mechanic["mode"] == "multiply" else damage.rstrip("+-")
        if mechanic and mechanic["kind"] == "discard_damage":
            damage = "0" if mechanic["mode"] == "multiply" else damage.rstrip("+")
        if (
            mechanic
            and mechanic["kind"] == "sequence"
            and mechanic["steps"][0]["kind"] == "damage_expression"
        ):
            damage = (
                "0"
                if mechanic["steps"][0]["mode"] == "multiply"
                else damage.rstrip("+-")
            )
        if "cost" not in p or not damage.isdigit() or not p.get("ename"):
            return None
        attacks.append(
            {
                "name": plain(p["ename"]),
                "cnName": plain(
                    p.get("ZHSname") or p.get("name") or p.get("ZHTname", "")
                ),
                "damage": int(damage),
                "cost": [ELEMENTS[c] for c in costs if c in ELEMENTS],
                **({"mechanic": mechanic, "text": p["effectZHS"]} if mechanic else {}),
            }
        )
    if (
        not attacks
        or len(attacks) > 2
        or any(
            a["damage"] <= 0
            and a.get("mechanic", {}).get("kind")
            not in ZERO_DAMAGE_EFFECTS | {"damage_expression"}
            for a in attacks
        )
    ):
        return None
    return {
        "name": english,
        **({"pokemonRule": rule} if rule != "NONE" else {}),
        **({"pokemonType": "EX", "prize": 2} if h.get("class") == "ex" else {}),
        "hp": int(h["hp"]),
        "type": ELEMENTS[h["type"]],
        "retreat": int(end["retreat"]),
        "stage": stage,
        "evolvesFrom": sorted(previous) if stage != "BASIC" else [],
        "weakness": [ELEMENTS[end["weakness"]]] if end.get("weakness") else [],
        "resistance": [ELEMENTS[end["resistance"]]] if end.get("resistance") else [],
        "attacks": attacks,
        **({"sourceCorrections": corrections} if corrections else {}),
        **({"abilities": powers} if powers else {}),
    }


def build(catalog, articles):
    groups = {}
    trainers = {}
    special_energies = {}
    english_names = {}
    for title, page in articles.items():
        intro = (page.get("text") or "").split("==")[0]
        identity = next(
            (
                params(t)
                for t in mw.parse(intro).filter_templates()
                if str(t.name).strip() == "N"
            ),
            {},
        )
        cn, en = plain(identity.get("1", "")), plain(identity.get("4", ""))
        if cn and en:
            # N templates sometimes omit regional/rule-box suffixes in Chinese.
            # The full article title is an exact alias; a bare species alias must
            # not conflate e.g. Eevee, Eevee V and Alolan/Galarian forms.
            english_names.setdefault(title.split("（", 1)[0], set()).add(en)
            special = re.search(
                r"(?:-GX|-EX|\bex|\bV|\bVMAX|\bVSTAR)$", en
            ) or re.match(r"(?:Alolan|Galarian|Hisuian|Paldean|Mega) ", en)
            if not special or re.search(
                r"ex|GX|EX|VMAX|VSTAR|阿罗拉|阿羅拉|伽勒尔|伽勒爾|洗翠|帕底亚|帕底亞",
                cn,
            ):
                english_names.setdefault(cn, set()).add(en)
    for c in catalog["cards"]:
        if (
            not includes(c)
            or (
                c["effectStatus"] == "verified"
                and not c["engineId"].startswith(("P4P-", "P4T-", "P4S-"))
            )
            or c.get("catalogStatus") not in ("listed", "mechanism-reviewed")
        ):
            continue
        if not c.get("releasedAt") or c["releasedAt"] > catalog["asOf"]:
            continue
        title = c.get("cardPage")
        page = articles.get(title, {})
        spec = compile_rule(page, english_names)
        if not spec:
            from scripts.cardpool.trainer_rules import compile_trainer

            spec = compile_trainer(page)
        if not spec:
            from scripts.cardpool.special_energy_rules import compile_energy
            spec = compile_energy(page)
        if (
            not spec
            or not page.get("revision")
            or c.get("mark") not in ("G", "H", "I", "J")
        ):
            continue
        # Exact CN printing/mark evidence is required in addition to rule grammar.
        from scripts.catalog.enrich import parse_page, number

        _, paired = parse_page(page["text"])
        from scripts.cardpool.source_corrections import apply as repair_printing
        printing_repairs = []
        for paired_row in paired:
            printing_repairs.extend(repair_printing(page, "reviewed-printing-row", paired_row))
        exact = [
            r
            for r in paired
            if r.get("cnicon") == c["productCode"]
            and number(r.get("cnno", "")) == c["collectorNumber"]
            and r.get("reg") == c["mark"]
        ]
        if not exact:
            continue
        key = hashlib.sha256(title.encode()).hexdigest()[:12].upper()
        is_trainer = "trainerType" in spec
        is_energy = "energyType" in spec
        row = (special_energies if is_energy else trainers if is_trainer else groups).setdefault(
            key,
            {
                **spec,
                "effectKey": ("P4S-" if is_energy else "P4T-" if is_trainer else "P4P-") + key,
                "cardPage": title,
                "revision": page["revision"],
                "sourceSha256": hashlib.sha256(page["text"].encode()).hexdigest(),
                "printings": [],
            },
        )
        row["printings"].append(c["printingId"])
        if printing_repairs and c.get("numberCorrection"):
            row.setdefault("printingCorrections", {})[c["printingId"]] = printing_repairs
    return {
        "schema": "p4-plain-pokemon-v1",
        "scope": "Source-matched Pokemon and Trainers with reviewed shared attacks, abilities and zone mechanisms; explicit ex/Ancient/Future/Tera/ACE SPEC context and evolution aliases; separately evidenced basic Energy printings. Unknown fields and rules remain blocked.",
        "cards": sorted(groups.values(), key=lambda r: r["effectKey"]),
        "trainers": sorted(trainers.values(), key=lambda r: r["effectKey"]),
        "specialEnergies": sorted(special_energies.values(), key=lambda r: r["effectKey"]),
        "basicEnergyPrintings": compile_basic_energies(catalog, articles),
    }


def compile_basic_energies(catalog, articles):
    """Bind numbered basic energies using their product rows and generic rule page.

    Basic Energy pages deliberately aggregate all artworks; their expansion list
    is not a complete printing index. The product row supplies printing identity.
    """
    mapping = {
        "grass": "P4E-001",
        "fire": "SVE-002",
        "water": "P4E-003",
        "lightning": "P4E-004",
        "psychic": "SVE-005",
        "fighting": "P4E-006",
        "darkness": "P4E-007",
        "metal": "SVE-008",
    }
    type_names = {
        "grass": "GRASS",
        "fire": "FIRE",
        "water": "WATER",
        "lightning": "LIGHTNING",
        "psychic": "PSYCHIC",
        "fighting": "FIGHTING",
        "darkness": "DARK",
        "metal": "METAL",
    }
    records = []
    for c in catalog["cards"]:
        kind = c.get("basicEnergyType")
        if (
            kind not in mapping
            or c.get("catalogStatus") not in ("listed", "mechanism-reviewed")
            or not includes(c)
            or not c.get("releasedAt")
            or c["releasedAt"] > catalog["asOf"]
            or not c.get("collectorNumber")
            or not c.get("sourceEvidence")
        ):
            continue
        from scripts.cardpool.basic_printing_evidence import verified_row
        recovered_row = None if c.get("sourceRow") else verified_row(c)
        if not c.get("sourceRow") and not recovered_row:
            continue
        page = articles.get(c.get("cardPage"), {})
        headers = [
            params(t)
            for t in mw.parse(page.get("text", "")).filter_templates()
            if str(t.name).strip() == "能量卡信息/header"
        ]
        if len(headers) != 1 or not page.get("revision"):
            continue
        h = headers[0]
        if (
            h.get("base") != "y"
            or ELEMENTS.get(h.get("type")) != type_names[kind]
            or plain(h.get("name", "")) != c["cnName"]
        ):
            continue
        records.append(
            {
                "printingId": c["printingId"],
                "effectKey": mapping[kind],
                "cardPage": c["cardPage"],
                "revision": page["revision"],
                "sourceSha256": hashlib.sha256(page["text"].encode()).hexdigest(),
                "identityBasis": "numbered-product-row-and-basic-energy-rule-page",
                **({"productRowEvidence": recovered_row} if recovered_row else {}),
            }
        )
    return records


if __name__ == "__main__":
    result = build(read(ROOT / "data/catalog/catalog.json"), load_articles())
    (ROOT / "data/cardpool/plain-pokemon.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        {
            "effects": len(result["cards"]),
            "printings": sum(len(r["printings"]) for r in result["cards"]),
        }
    )
