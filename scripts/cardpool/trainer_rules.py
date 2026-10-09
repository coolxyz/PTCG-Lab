"""Reviewed modern Item/Supporter rules; historical variants are not inferred."""

import re
import mwparserfromhell as mw
from scripts.catalog.enrich import params, plain


def compile_effect(en, cn):
    from scripts.cardpool.trainer_field_rules import compile_field

    field = compile_field(en, cn)
    if field:
        return field
    from scripts.cardpool.trainer_expansion_rules import compile_expansion

    expanded = compile_expansion(en, cn)
    if expanded:
        return expanded
    conditional = [
        (
            "Draw 2 cards. If your opponent's Active Pokémon is a Pokémon ex, draw 2 more cards.",
            "从自己牌库上方抽取2张卡牌。如果对手的战斗宝可梦是「{{TCG|宝可梦ex}}」的话，则额外抽取2张卡牌。",
            {"kind": "draw", "count": 2, "condition": "opponent_ex", "bonus": 2},
        ),
        (
            "Draw cards until you have 5 cards in your hand. If none of your Pokémon have any Energy attached, draw cards until you have 7 cards in your hand instead.",
            "从牌库上方抽取卡牌，直到自己的手牌变为5张为止。如果自己场上的宝可梦身上未附着能量的话，则直到手牌数量变为7张为止。",
            {
                "kind": "draw_until",
                "count": 5,
                "condition": "no_attached_energy",
                "bonus": 2,
            },
        ),
        (
            "Shuffle your hand into your deck. Then, draw 4 cards. If your opponent has 3 or fewer Prize cards remaining, draw 8 cards instead.",
            "将自己的手牌全部放回牌库并重洗牌库。然后。从牌库上方抽取4张卡牌。如果对手的剩余奖赏卡张数在3张及以下的话，则抽取的张数变为8张。",
            {
                "kind": "shuffle_draw",
                "count": 4,
                "condition": "opponent_three_prizes",
                "bonus": 4,
            },
        ),
        (
            "Shuffle your hand into your deck. Then, flip a coin. If heads, draw 8 cards. If tails, draw 3 cards.",
            "将自己的手牌全部放回牌库并重洗牌库。然后，抛掷1次硬币，如果为正面，则从牌库上方抽取8张卡牌，如果为反面，则从牌库上方抽取3张卡牌。",
            {"kind": "shuffle_draw", "count": 3, "coinBonus": 5},
        ),
        (
            "Count the cards in your hand, shuffle those cards into your deck, then draw that many cards plus 1.",
            "数过自己的手牌后，将其全部放回牌库并重洗牌库。然后，从牌库上方抽取比放回牌库的卡牌张数多1张的卡牌。",
            {"kind": "shuffle_draw", "count": 1, "handCount": True},
        ),
        (
            "Heal 30 damage from each Pokémon ''(both yours and your opponent's).''",
            "将双方所有宝可梦的HP，各回复「30」。",
            {"kind": "heal_all", "count": 30},
        ),
        (
            "Switch your Active Pokémon with 1 of your Benched Pokémon. If you do, draw cards until you have 5 cards in your hand.",
            "将自己的战斗宝可梦与备战宝可梦互换。然后，从牌库上方抽取卡牌，直到自己的手牌变为5张为止。",
            {"kind": "switch", "thenDrawUntil": 5},
        ),
    ]
    for english, chinese, result in conditional:
        if en == english and cn == chinese:
            return result
    # These wrappers are costs/conditions, not effects that can be skipped.
    for ep, cp, extra in [
        (
            r"You can use this card only if you discard 3 other cards from your hand\.<br><br>",
            r"这张卡牌，只有将自己的3张手牌放于弃牌区后才可使用。<br><br>",
            {"discardCost": 3},
        ),
        (
            r"You can use this card only if you discard another card from your hand\.\s*<br><br>",
            r"这张卡牌，只有将自己的1张手牌放于弃牌区后才可使用。<br><br>",
            {"discardCost": 1},
        ),
        (
            r"You can use this card only if you discard 2 other cards from your hand\.<br>(?:<br>)?",
            r"这张卡牌，只有将自己的2张手牌放于弃牌区后才可使用。<br><br>",
            {"discardCost": 2},
        ),
        (
            r"You can use this card only when it is the last card in your hand\.\s*<br><br>",
            r"这张卡牌，只有在自己的手牌只剩这1张时才可使用。<br><br>",
            {"lastHand": True},
        ),
        (
            r"You can use this card only if any of your Pokémon were Knocked Out during your opponent's last turn\.<br><br>",
            r"这张卡牌，只有在上一个对手的回合，自己的宝可梦'''(?:{{TCG\|昏厥}}|昏厥)'''时才可使用。<br><br>",
            {"afterKnockout": True},
        ),
    ]:
        e, c = re.match(ep, en), re.match(cp, cn)
        if e and c:
            result = compile_effect(en[e.end() :], cn[c.end() :])
            return {**result, **extra} if result else None
    if en.endswith(" Your turn ends.") and cn.startswith(
        "如果使用了这张卡牌的话，则自己的回合结束。<br><br>"
    ):
        result = compile_effect(
            en[: -len(" Your turn ends.")], cn.split("<br><br>", 1)[1]
        )
        return {**result, "endTurn": True} if result else None
    if (
        en
        in (
            "Switch in 1 of your opponent's Benched Pokémon to the Active Spot.",
            "Switch 1 of your opponent's Benched Pokémon with their Active Pokémon.",
        )
        and cn == "选择对手的1只备战宝可梦，将其与战斗宝可梦互换。"
    ):
        return {"kind": "gust"}
    if (
        en
        == "Put a Pokémon or a Basic Energy card from your discard pile into your hand."
        and cn
        == "选择自己弃牌区中的1张宝可梦或1张基本能量，在给对手看过之后，加入手牌。"
    ):
        return {"kind": "recover_hand", "filter": "pokemon_or_energy", "count": 1}
    if (
        en == "Put up to 2 Nemona cards from your discard pile into your hand."
        and cn
        == "选择自己弃牌区中最多2张「{{TCG|妮莫}}」，在给对手看过之后，加入手牌。"
    ):
        return {"kind": "recover_hand", "filter": "name:Nemona", "count": 2}
    if (
        en
        == "Search your deck for a Stadium card and an Energy card, reveal them, and put them into your hand. Then, shuffle your deck."
        and cn
        == "选择自己牌库中的竞技场和能量各1张，在给对手看过之后，加入手牌。并重洗牌库。"
    ):
        return {"kind": "search_pair", "filters": ["stadium", "energy"], "count": 1}
    if (
        en
        == "Search your deck for a Tera Pokémon, reveal it, and put it into your hand. Then, shuffle your deck."
        and cn
        == "选择自己牌库中的1张「{{TCG|太晶}}」宝可梦，在给对手看过之后，加入手牌。并重洗牌库。"
    ):
        return {"kind": "search_hand", "filter": "tera", "count": 1}
    if (
        en
        == "Search your deck for a Pokémon that doesn't have a Rule Box, reveal it, and put it into your hand. Then, shuffle your deck. ''(Pokémon ex, Pokémon V, etc. have Rule Boxes.)''"
        and cn
        == "选择自己牌库中的1张宝可梦（除「{{TCG|拥有规则的宝可梦}}」外），在给对手看过之后加入手牌。并重洗牌库。"
    ):
        return {"kind": "search_hand", "filter": "no_rule", "count": 1}
    if (
        en
        == "Search your deck for up to 3 Basic Pokémon with 120 HP or less, reveal them, and put them into your hand. Then, shuffle your deck."
        and cn
        == "从自己牌库中，选择最多3张HP在「120」及以下的'''基础'''宝可梦，在给对手看过之后，加入手牌。并重洗牌库。"
    ):
        return {"kind": "search_hand", "filter": "basic_120", "count": 3}
    if (
        en
        == "Search your deck for up to 2 cards and put them into your hand. Then, shuffle your deck."
        and cn == "选择自己牌库中任意卡牌最多2张，加入手牌。并重洗牌库。"
    ):
        return {"kind": "search_hand", "filter": "any", "count": 2, "reveal": False}
    if (
        en
        == "Search your deck for a Pokémon, reveal it, and put it into your hand. Then, shuffle your deck."
        and cn == "选择自己牌库中的1张宝可梦，在给对手看过之后，加入手牌。并重洗牌库。"
    ):
        return {"kind": "search_hand", "filter": "pokemon", "count": 1}
    if (
        en
        == "Shuffle up to 5 Pokémon from your discard pile into your deck. If you shuffled any cards into your deck in this way, draw 3 cards."
        and cn
        == "选择自己弃牌区中最多5张宝可梦，在给对手看过之后，放回牌库并重洗牌库。然后，从牌库上方抽取3张卡牌。"
    ):
        return {"kind": "recover_deck", "filter": "pokemon", "count": 5, "thenDraw": 3}
    for pattern, chinese, kind in [
        (r"Draw ([1-9]\d*) cards\.", r"从自己牌库上方抽取([1-9]\d*)张卡牌。", "draw"),
        (
            r"Discard your hand and draw ([1-9]\d*) cards\.",
            r"将自己的手牌全部放于弃牌区，从牌库上方抽取([1-9]\d*)张卡牌。",
            "discard_draw",
        ),
        (
            r"Each player shuffles his or her hand into his or her deck and draws ([1-9]\d*) cards\.",
            r"双方玩家，各将所有手牌放回牌库并重洗牌库。然后，各从牌库上方抽取([1-9]\d*)张卡牌。",
            "both_shuffle_draw",
        ),
        (
            r"Heal ([1-9]\d*) damage from 1 of your Pokémon\.",
            r"回复自己1只宝可梦「([1-9]\d*)」HP。",
            "heal",
        ),
        (
            r"Draw cards until you have ([1-9]\d*) cards in your hand\.",
            r"从牌库上方抽取卡牌，直到自己的手牌变为([1-9]\d*)张为止。",
            "draw_until",
        ),
        (
            r"Shuffle your hand into your deck\. Then, draw ([1-9]\d*) cards\.",
            r"将自己的手牌全部放回牌库并重洗牌库。然后，从牌库上方抽取([1-9]\d*)张卡牌。",
            "shuffle_draw",
        ),
    ]:
        e, c = re.fullmatch(pattern, en), re.fullmatch(chinese, cn)
        if e and c and e[1] == c[1]:
            return {"kind": kind, "count": int(e[1])}
    if (
        en == "Switch your Active Pokémon with 1 of your Benched Pokémon."
        and cn == "将自己的战斗宝可梦与备战宝可梦互换。"
    ):
        return {"kind": "switch"}
    if en.startswith(
        "If you go first, you may use this card during your first turn.<br><br>"
    ) and cn.startswith("这张卡牌，即使是先攻玩家的最初回合也可以使用。<br><br>"):
        rule = compile_effect(en.split("<br><br>", 1)[1], cn.split("<br><br>", 1)[1])
        if rule and rule["kind"] == "discard_draw":
            return {**rule, "allowFirstTurn": True}
    for english, chinese, category in [
        ("Pokémon", "宝可梦", "pokemon"),
        ("Supporter cards", "{{TCG|支援者卡|支援者}}", "supporter"),
        ("basic Energy cards", "基本能量", "basic_energy"),
    ]:
        e = re.fullmatch(
            "Shuffle up to ([1-9]\\d*) "
            + re.escape(english)
            + r" from your discard pile into your deck\.",
            en,
        )
        c = re.fullmatch(
            "选择自己弃牌区中最多([1-9]\\d*)张"
            + re.escape(chinese)
            + "，在给对手看过之后，?放回牌库并重洗牌库。",
            cn,
        )
        if e and c and e[1] == c[1]:
            return {"kind": "recover_deck", "filter": category, "count": int(e[1])}
    for english, chinese, category in [
        ("Stage 1 Pokémon", "'''1阶进化'''宝可梦", "stage1"),
        ("Future Pokémon", "「{{TCG|未来}}」宝可梦", "future"),
        ("Item cards", "物品", "item"),
        ("Evolution Pokémon", "进化宝可梦", "evolution"),
        ("Pokémon", "宝可梦", "pokemon"),
        ("Basic Energy cards", "基本能量", "basic_energy"),
    ]:
        e = re.fullmatch(
            "Search your deck for up to ([1-9]\\d*) "
            + re.escape(english)
            + r", reveal them, and put them into your hand\. Then, shuffle your deck\.",
            en,
        )
        c = re.fullmatch(
            "选择自己牌库中最多([1-9]\\d*)张"
            + re.escape(chinese)
            + "，在给对手看过之后，加入手牌。并重洗牌库。",
            cn,
        )
        if e and c and e[1] == c[1]:
            return {"kind": "search_hand", "filter": category, "count": int(e[1])}
    if (
        en
        == "Search your deck for a Basic Pokémon and put it onto your Bench. Then, shuffle your deck."
        and cn == "将自己牌库中1张'''基础'''宝可梦，放于备战区。并重洗牌库。"
    ):
        return {"kind": "search_bench", "count": 1, "filter": "basic_pokemon"}
    if (
        en
        == "Search your deck for an Item card and a Pokémon Tool card, reveal them, and put them into your hand. Then, shuffle your deck."
        and cn
        == "选择自己牌库中「{{TCG|物品卡|物品}}」和「{{TCG|宝可梦道具}}」各1张，在给对手看过之后，加入手牌。并重洗牌库。"
    ):
        return {"kind": "search_pair", "filters": ["item", "tool"], "count": 1}
    return None


def compile_trainer(page):
    from scripts.cardpool.technical_machine_rules import compile_machine
    machine = compile_machine(page)
    if machine:
        return machine
    text = re.sub(r"<!--.*?-->", "", page.get("text") or "", flags=re.S)
    text = re.sub(r"==\s*卡牌信息\s*==", "==卡牌信息==", text)
    if "==卡牌信息==" not in text:
        return None
    body = text.split("==卡牌信息==", 1)[1].split("{{ExpansionList", 1)[0]
    body = body.split("=== e卡信息", 1)[0]
    # Tables are presentation wrappers; only card-family templates are semantic.
    templates = [t for t in mw.parse(body).filter_templates() if str(t.name).strip().startswith("训练家卡信息/")]
    names = [str(t.name).strip() for t in templates]
    header_names = {
        "训练家卡信息/header",
        "训练家卡信息/header/KM",
        "训练家卡信息/header/ACESPEC/SV",
    }
    if set(names) - (
        header_names
        | {"训练家卡信息/main", "训练家卡信息/multimain", "训练家卡信息/end", "-"}
    ):
        return None
    headers = [params(t) for t in templates if str(t.name).strip() in header_names]
    mains = [
        params(t)
        for t in templates
        if str(t.name).strip() in ("训练家卡信息/main", "训练家卡信息/multimain")
    ]
    if len(headers) > 1 and len(headers) == len(mains) and mains[0].get("ver1", "").startswith("朱&紫"):
        # Some pages retain a second, historical Item-era Tool section.
        # GHIJ uses the explicitly labelled Scarlet/Violet section only.
        headers, mains = headers[:1], mains[:1]
    elif (len(headers) == len(mains) == 2 and headers[0].get("type") == "宝可梦道具"
          and headers[1].get("type") == "物品卡" and headers[1].get("type2") == "宝可梦道具"
          and re.match(r"SV[0-9]", headers[0].get("image", ""))):
        headers, mains = headers[:1], mains[:1]
    if len(headers) != 1 or len(mains) != 1:
        return None
    h, p = headers[0], mains[0]
    if "HP" in h and "hp" not in h:
        h["hp"] = h.pop("HP")
    category = {"支援者卡": "supporter", "物品卡": "item", "宝可梦道具": "tool", "竞技场卡": "stadium", "競技場卡": "stadium"}.get(
        h.get("type")
    )
    if h.get("type2") == "宝可梦道具" and category == "item":
        category = "tool"
    elif h.get("type2") and h.get("type2") != h.get("type"):
        return None
    if not category or any(
        k not in {"type", "type2", "name", "image", "disableEN", "ball", "class", "link", "hp"} and v
        for k, v in h.items()
    ):
        return None
    if h.get("ball") not in (None, "", "yes", "y") or h.get("class") not in (
        None,
        "",
        "古代",
        "未来",
    ):
        return None
    from scripts.cardpool.tool_rules import compile_tool
    from scripts.cardpool.stadium_rules import compile_stadium

    compiler = compile_stadium if category == "stadium" else compile_tool if category == "tool" else compile_effect
    from scripts.cardpool.source_text import chinese_effect
    cn = chinese_effect(p)
    rule = compiler(p.get("eeffect", ""), cn)
    if not rule or h.get("hp") and (rule.get("kind") != "setup_doll" or h["hp"] != "120"):
        return None
    intro = mw.parse(text.split("==卡牌信息==", 1)[0]).filter_templates()
    identity = next((params(t) for t in intro if str(t.name).strip() == "N"), {})
    name = plain(identity.get("4", ""))
    if not name:
        return None
    return {
        "name": name,
        "trainerType": category,
        "mechanic": rule,
        "text": cn,
        "aceSpec": "训练家卡信息/header/ACESPEC/SV" in names,
        "trait": {"古代": "ANCIENT", "未来": "FUTURE"}.get(h.get("class")),
    }
