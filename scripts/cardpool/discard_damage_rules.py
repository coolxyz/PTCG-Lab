"""Whole discard-to-damage attacks; card counts are distinct from Energy units."""

import re
import json
from pathlib import Path
CLAUSES=json.loads(Path(__file__).with_name("discard-damage-clauses.json").read_text(encoding="utf8"))
from scripts.cardpool.extended_math_rules import ELEMENTS


def compile_discard_damage(p):
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
    for c in CLAUSES:
        r=c["rule"]
        if en==c["english"] and cn==c["chinese"] and mode==r["mode"] and (mode=="add" or p.get("damage","").rstrip("×")==str(r["factor"])):
            return dict(r)
    e = re.fullmatch(
        r"(.*?) This attack does ([1-9]\d*) (more )?damage for each card you discarded in this way\.",
        en,
    )
    c = re.fullmatch(r"(.*?)，(追加)?造成其张数×([1-9]\d*)伤害。", cn)
    if (
        not e
        or not c
        or e[2] != c[3]
        or bool(e[3]) != (mode == "add")
        or bool(c[2]) != (mode == "add")
    ):
        return None
    if mode == "multiply" and p.get("damage", "").rstrip("×") != e[2]:
        return None
    pairs = [
        ("Energy", "能量", {}),
        ("Basic Energy", "基本能量", {"basic": True}),
    ]
    for eng, ch, element in ELEMENTS:
        for alias in (eng, ch):
            pairs.extend(
                [
                    (
                        f"{{{{e|{alias}}}}} Energy",
                        f"{{{{e|{ch}}}}}能量",
                        {"type": element},
                    ),
                    (
                        f"{{{{e|{alias}}}}} Energy cards",
                        f"{{{{e|{ch}}}}}能量",
                        {"type": element},
                    ),
                ]
            )
    for english, chinese, filter_ in pairs:
        for zone, ez, cz in [
            ("self", "this Pokémon", "这只宝可梦"),
            ("field", "your Pokémon", "自己场上宝可梦"),
            ("bench", "your Benched Pokémon", "自己备战宝可梦"),
        ]:
            prefix = "若希望，可" if mode == "add" else ""
            ep = (
                r"(?:You may discard|Discard) up to ([1-9]\d*) "
                + re.escape(english)
                + " from "
                + re.escape(ez)
                + r"\."
            )
            cp = (
                re.escape(prefix + "将" + cz + "身上附着的最多")
                + r"([1-9]\d*)张"
                + re.escape(chinese + "放于弃牌区")
            )
            me, mc = re.fullmatch(ep, e[1]), re.fullmatch(cp, c[1])
            if me and mc and me[1] == mc[1]:
                return {
                    "kind": "discard_damage",
                    "mode": mode,
                    "factor": int(e[2]),
                    "maxCards": int(me[1]),
                    "zone": zone,
                    **filter_,
                }
            if (
                e[1] == f"You may discard any amount of {english} from {ez}."
                and c[1] == f"将{cz}身上附着的任意数量的{chinese}放于弃牌区"
            ):
                return {
                    "kind": "discard_damage",
                    "mode": mode,
                    "factor": int(e[2]),
                    "zone": zone,
                    **filter_,
                }
            if (
                e[1] == f"Discard all {english} from {ez}."
                and c[1] == f"将{cz}身上附着的{chinese}全部放于弃牌区"
            ):
                return {
                    "kind": "discard_damage",
                    "mode": mode,
                    "factor": int(e[2]),
                    "zone": zone,
                    "mandatoryAll": True,
                    **filter_,
                }
    if (
        e[1] == "Discard any number of Pokémon Tool cards from your hand."
        and c[1] == "将自己手牌中任意数量的「{{TCG|宝可梦道具}}」放于弃牌区"
    ):
        return {
            "kind": "discard_damage",
            "mode": mode,
            "factor": int(e[2]),
            "zone": "hand",
            "tools": True,
        }
    if (
        e[1]
        == "Before doing damage, you may discard any number of Pokémon Tools from your Pokémon."
        and c[1]
        == "在造成伤害前，将任意数量的放于自己场上宝可梦身上的「{{TCG|宝可梦道具}}」放于弃牌区"
    ):
        return {
            "kind": "discard_damage",
            "mode": mode,
            "factor": int(e[2]),
            "zone": "field",
            "tools": True,
        }
    return None
