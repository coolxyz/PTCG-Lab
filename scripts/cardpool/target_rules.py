"""Complete bilingual targeted attacks; damage selection is distinct from effects."""

import re


def compile_target(p):
    en, cn = p.get("eeffect", ""), p.get("effectZHS", "")
    if (
        not p.get("damage")
        and not p.get("damageP")
        and (
            en
            == "This attack does 50 damage to 2 of your opponent's Pokémon. This attack's damage isn't affected by Weakness or Resistance, or by any effects on those Pokémon."
            and cn
            == "给对手的2只宝可梦，各造成50伤害。这个招式的伤害，不计算弱点、抗性，以及受到伤害的宝可梦身上所附加的效果。"
        )
    ):
        return {
            "kind": "target_damage",
            "amount": 50,
            "count": 2,
            "zone": "all",
            "ignoreEffects": True,
            "ignoreWeaknessResistance": True,
        }
    # An unmatched italic delimiter changes presentation only.
    if en.endswith(" ''(Don't apply Weakness and Resistance for Benched Pokémon.)"):
        en += "''"
    discard = None
    e = re.match(r"Discard ([1-9]\d*) Energy from this Pokémon\. ", en)
    c = re.match(r"将这只宝可梦身上附着的([1-9]\d*)个能量放于弃牌区，", cn)
    if e and c and e[1] == c[1]:
        discard = int(e[1])
        en = en[e.end() :]
        cn = cn[c.end() :]
    elif e or c:
        return None
    en = en.replace(".'' (", ". ''(").replace("damage to one of", "damage to 1 of")
    en_suffix = " ''(Don't apply Weakness and Resistance for Benched Pokémon.)''"
    cn_suffix = "''[备战宝可梦不计算弱点、抗性。]''"
    if not en.endswith(en_suffix) or not cn.endswith(cn_suffix):
        return None
    en, cn = en[: -len(en_suffix)], cn[: -len(cn_suffix)]
    if p.get("damage") or p.get("damageP"):
        return None
    for zone, eng, ch in [
        ("all", "Pokémon", "宝可梦"),
        ("bench", "Benched Pokémon", "备战宝可梦"),
    ]:
        e = re.fullmatch(
            r"This attack does ([1-9]\d*) damage to ([1-9]\d*) of your opponent's "
            + eng
            + r"\.",
            en,
        )
        c = re.fullmatch(
            r"给对手的([1-9]\d*)只" + ch + r"，?(?:各)?造成([1-9]\d*)伤害。", cn
        )
        if e and c and e[1] == c[2] and e[2] == c[1]:
            return {
                "kind": "target_damage",
                "amount": int(e[1]),
                "count": int(e[2]),
                "zone": zone,
                **({"discardEnergy": discard} if discard else {}),
            }
        for english, chinese, term in [
            (
                "damage counter on this Pokémon",
                "这只宝可梦身上放置的伤害指示物数量",
                "self_counters",
            ),
            (
                "Prize card your opponent has taken",
                "对手已经获得的奖赏卡张数",
                "opponent_prizes_taken",
            ),
        ]:
            e = re.fullmatch(
                r"This attack does ([1-9]\d*) damage to 1 of your opponent's "
                + eng
                + " for each "
                + re.escape(english)
                + r"\.",
                en,
            )
            c = re.fullmatch(
                "给对手的1只"
                + ch
                + "，造成"
                + re.escape(chinese)
                + r"×([1-9]\d*)伤害。",
                cn,
            )
            if e and c and e[1] == c[1] and discard is None:
                return {
                    "kind": "target_damage",
                    "amount": int(e[1]),
                    "term": term,
                    "count": 1,
                    "zone": zone,
                }
    return None
