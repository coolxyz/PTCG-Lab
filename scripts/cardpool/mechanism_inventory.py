"""Review backlog, not an effect compiler or an equivalence certificate."""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import mwparserfromhell as mw  # noqa: E402
from scripts.catalog.enrich import params  # noqa: E402
from scripts.cardpool.catalog_inventory import load_articles, read  # noqa: E402
from scripts.cardpool.scope_inventory import build as scope_inventory  # noqa: E402

# Multi-label families describe reusable primitives. They never authorize cards.
FAMILIES = {
    "draw_hand": ("抽牌与手牌调整", r"draw|hand|抽|手牌"),
    "search_reveal": (
        "检索、查看、展示与洗牌",
        r"search|look at|reveal|shuffle|牌库|牌庫|重洗|展示",
    ),
    "energy_move": ("附能、弃能与供能条件", r"energy|能量|エネルギー"),
    "zone_move": (
        "弃牌、回收、回手与放逐",
        r"discard|lost zone|return|put .* into|弃牌|棄牌|放回|放逐|加入",
    ),
    "switch_retreat": ("换位与撤退", r"switch|retreat|换|換|撤退"),
    "damage_math": ("伤害数值、条件与修正", r"damage|伤害|傷害"),
    "counters_heal": (
        "伤害指示物、回复与HP",
        r"counter|heal|\bhp\b|指示物|回复|恢復|恢复",
    ),
    "coin_random": ("硬币与随机分支", r"coin|random|硬币|硬幣"),
    "status": (
        "特殊状态",
        r"poison|burn|confus|asleep|sleep|paraly|中毒|灼伤|灼傷|混乱|混亂|睡眠|麻痹",
    ),
    "restriction_protection": (
        "持续限制、保护与替代",
        r"prevent|can.t|cannot|less damage|next turn|无法|無法|不会受到|不受到|下一个|下個|不能",
    ),
    "evolution": ("进化与退化", r"evolv|evolution|进化|進化|退化"),
    "prizes_win": (
        "奖赏、击倒与胜负",
        r"prize|knock|win|奖赏|獎賞|气绝|氣絕|昏厥|胜利",
    ),
    "copy": (
        "复制招式与特性",
        r"copy|choose .*attack|use .*attack|复制|複製|选择.*招式",
    ),
    "ability_lifecycle": (
        "特性触发、次数、抑制与持续",
        r"ability|abilities|once during|特性|每回合|每个回合",
    ),
    "special_rules": (
        "特殊规则、形态与牌型",
        r"vstar|vmax|\bex\b|ace spec|太晶|光辉|光輝|规则|規則",
    ),
}
COSMETIC = {
    "name",
    "ename",
    "jname",
    "ZHSname",
    "ZHTname",
    "image",
    "link",
    "ndex",
    "species",
    "height",
    "weight",
    "dex",
    "jdex",
    "edex",
    "ZHSdex",
    "ZHTdex",
    "ZHAlt",
    "disableEN",
}

# Published mechanism labels are stronger evidence than keyword matches, but
# describe reusable operations, not identical card semantics.
IMPLEMENTED_FAMILIES = {
    "ability_lifecycle": "ability_draw ability_discard_draw ability_bottom_hand_draw ability_both_draw ability_self_counter_draw ability_attach_draw ability_switch ability_armor",
    "trainer_play": "trainer legacy_trainer look_hand discard_hand_to opponent_hand reveal_draw opponent_status heal_own_all",
    "draw_hand": "draw hand_reset disruption discard_hand draw_choice draw_until",
    "search_reveal": "search_basic search_item search_tool search_supporter search_pokemon search_energy search_any order_deck search_hand search_bench search_attach",
    "energy_move": "basic_energy conditional_energy energy_transfer recover_energy discard_energy_damage coin_discard_energy discard_typed_energy discard_self_energy discard_opponent_energy search_attach",
    "zone_move": "discard_hand discard_cost recover_energy recover_card return_to_deck discard_energy_damage coin_discard_energy discard_typed_energy mill_opponent discard_self_energy discard_opponent_energy search_hand search_bench search_attach mill_self discard_stadium discard_draw sequence",
    "switch_retreat": "gust switch retreat_modifier opponent_switch prevent_retreat self_switch",
    "damage_math": "attack attack_damage discard_energy_damage coin_bonus coin_count coin_fail coin_until_tails damage_shield damage_expression recoil ignore_damage_modifiers bench_damage spread_damage",
    "counters_heal": "move_damage bench_counters heal_self heal_field place_counters",
    "coin_random": "coin_bonus coin_count coin_fail coin_until_tails coin_discard_energy",
    "restriction_protection": "prevent_retreat damage_shield attack_lock ignore_damage_modifiers",
    "status": "special_status recover_status",
    "evolution": "evolution evolve_bench",
    "prizes_win": "knockout_trigger knockout_condition prize_condition",
    "copy": "copy_attack",
    "special_rules": "stadium tool_attack conditional_energy pokemon_rule",
}

for family, additional in {
    'ability_lifecycle':'ability_search_any ability_draw_until ability_heal ability_protection ability_continuous ability_search_hand ability_reveal_opponent_hand ability_recover_active_status ability_poison_active ability_on_entry ability_activated_effect',
    "draw_hand": "discard_draw both_shuffle_draw shuffle_draw ability_draw ability_discard_draw ability_bottom_hand_draw ability_both_draw ability_self_counter_draw ability_attach_draw",
    "search_reveal": "search_pair reveal_opponent_hand",
    "energy_move": "move_self_energy return_self_energy ability_attach_draw ability_attach_energy ability_search_attach ability_search_bench attach_multiple",
    "zone_move": "recover_deck recover_hand random_discard_hand discard_tools_before_damage move_self_energy return_self_energy",
    "switch_retreat": "ability_switch",
    "damage_math": "target_damage ability_armor tool_modifier ability_continuous own_bench_damage gust_damage discard_damage",
    "counters_heal": "heal heal_all drain_damage ability_self_counter_draw ability_continuous tool_modifier",
    "prizes_win": "attack_extra_prize ability_prize_reduction ability_survive_damage",
    "coin_random": "random_discard_hand ability_coin_armor",
    "restriction_protection": "named_attack_lock opponent_attack_lock ability_armor attack_protection ability_protection item_lock ability_suppress_abilities",
}.items():
    IMPLEMENTED_FAMILIES[family] += " " + additional

# Shared dispatchers may span families; lifecycle and timing remain explicit
# in the compiled semantics instead of being collapsed by this index.
for family, additional in {
    "ability_lifecycle": "ability_checkup ability_end_turn ability_hand_event ability_move_active ability_hand_bench ability_lucky_prize ability_zero_to_hero ability_festival_lead",
    "copy": "ability_borrow_bench ability_borrow_evolutions",
    "special_rules": "special_energy setup_doll ability_dual_type ability_field_capacity",
    "evolution": "ability_hero_only ability_evolution_permission ability_zero_to_hero",
    "energy_move": "discard_field_energy transfer_energy special_energy",
    "zone_move": "return_pokemon field_operation extended_field trainer_operation ability_hand_bench",
    "restriction_protection": "attack_contract conditional_attack ability_hand_lock ability_no_discard_hand ability_no_field_hand ability_stadium_protection ability_status_immunity ability_trainer_protection ability_healing_block",
    "damage_math": "advanced_attack staged_attack conditional_attack ability_retaliate ability_damage_choice ability_lava_zone",
    "coin_random": "coin_branch",
    "status": "ability_persistent_poison ability_sleep_modifier ability_checkup ability_status_immunity",
    "prizes_win": "ability_knockout_prizes ability_knockout_retaliate ability_lucky_prize",
    "counters_heal": "ability_healing_block ability_lava_zone ability_damage_choice",
    "switch_retreat": "ability_move_active return_pokemon",
}.items():
    IMPLEMENTED_FAMILIES[family] += " " + additional


def implemented_effects(release, targets):
    supported = {c["printingId"] for c in targets if c["supported"]}
    target_ids = {c["printingId"] for c in targets}
    known = {m for words in IMPLEMENTED_FAMILIES.values() for m in words.split()}
    records = []
    for effect in release["effects"]:
        mechanisms = effect.get("mechanisms", [])
        families = {
            f
            for f, words in IMPLEMENTED_FAMILIES.items()
            if set(mechanisms) & set(words.split())
        }
        if effect.get("semantics", {}).get("abilities"):
            families.add("ability_lifecycle")
        unknown = sorted(set(mechanisms) - known)
        if unknown or not families:
            families.add("unclassified")
        records.append(
            {
                "effectKey": effect["effectKey"],
                "name": effect.get("name"),
                "implementation": effect.get("implementation"),
                "evidenceSuite": effect.get("evidenceSuite"),
                "releaseStatus": effect.get("status"),
                "interactionAudit": effect.get("interactionAudit"),
                "mechanisms": mechanisms,
                "unmappedMechanisms": unknown,
                "families": sorted(families),
                "printings": effect["printings"],
                "supportedPrintings": [
                    p for p in effect["printings"] if p in supported
                ],
                "outsideTargetPrintings": [
                    p for p in effect["printings"] if p not in target_ids
                ],
                "classificationBasis": "published-mechanisms-and-ability-presence",
                "autoApproval": False,
            }
        )
    return records


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def unit_kind(name):
    if "/header" in name or name.endswith("end"):
        return "card_context"
    if "/attack" in name:
        return "attack"
    if "/power" in name or "VSTAR力量" in name:
        return "ability"
    if "训练家卡信息/" in name or "能量卡信息/" in name:
        return "trainer_energy"
    return "card_context"


def classify(name, fields):
    text = " ".join(fields.values())
    found = {
        key for key, (_, pattern) in FAMILIES.items() if re.search(pattern, text, re.I)
    }
    if unit_kind(name) == "ability":
        found.add("ability_lifecycle")
    if any(x in name for x in ("/V", "/GX", "太晶", "光辉", "光輝")):
        found.add("special_rules")
    if fields.get("damage", "").isdigit():
        found.add("damage_math")
    if any("effect" in k.lower() for k in fields) and not found:
        found.add("unclassified")
    return sorted(found)


def extract(page):
    text = page.get("text") or ""
    if "==卡牌信息==" not in text:
        return [], ["RULE_BODY_MISSING"]
    body = text.split("==卡牌信息==", 1)[1].split("{{ExpansionList", 1)[0]
    parsed = mw.parse(body)
    units, gaps = [], []
    # Keep every template, including unknown ones, to avoid silent omissions.
    for index, template in enumerate(parsed.filter_templates(recursive=False)):
        name = str(template.name).strip()
        raw = params(template)
        fields = {
            k: v.strip() for k, v in raw.items() if v.strip() and k not in COSMETIC
        }
        kind = unit_kind(name)
        if kind == "card_context" and not any(
            x in name for x in ("卡牌信息/", "训练家卡信息/", "能量卡信息/")
        ):
            gaps.append("UNKNOWN_TEMPLATE:" + name)
        if "multimain" in name or any(
            re.search(r"effect\d+$", k, re.I) or re.search(r"effectZHS\d+$", k)
            for k in fields
        ):
            gaps.append("MULTI_VERSION_RULE_REVIEW")
        if not fields:
            continue
        semantic = {"template": name, "kind": kind, "fields": fields}
        pattern = {k: re.sub(r"\d+", "<N>", v) for k, v in fields.items()}
        units.append(
            {
                "index": index,
                **semantic,
                "families": classify(name, fields),
                "exactGroup": digest(semantic),
                "parameterGroup": digest(
                    {"template": name, "kind": kind, "fields": pattern}
                ),
                "numbers": {
                    k: re.findall(r"\d+", v)
                    for k, v in fields.items()
                    if re.search(r"\d", v)
                },
            }
        )
    residual = str(parsed.strip_code()).strip()
    if residual:
        gaps.append("NON_TEMPLATE_RULE_TEXT")
        units.append(
            {
                "index": len(units),
                "kind": "unparsed",
                "template": "prose",
                "fields": {"text": residual},
                "families": ["unclassified"],
                "exactGroup": digest(residual),
                "parameterGroup": digest(residual),
                "numbers": {},
            }
        )
    if not units:
        gaps.append("RULE_UNITS_MISSING")
    return units, sorted(set(gaps))


def ability_bucket(unit):
    """Coarse review hints, explicitly not executable trigger definitions."""
    text = " ".join(unit["fields"].values())
    patterns = {
        "enter_from_hand": r"when you play.*from your hand|从手牌.*备战|從手牌.*備戰",
        "evolution_trigger": r"when.*evolv|进化时|進化時",
        "knockout_trigger": r"when.*knocked out|气绝时|氣絕時",
        "between_turns": r"between turns|checkup|宝可梦检查|寶可夢檢查",
        "activated_in_turn": r"during your turn|在自己的回合|在自己的回合",
        "continuous": r"as long as|只要|只要",
        "once_per_turn_text": r"once during|1次|一次",
        "once_per_game_text": r"once per game|这场对战.*1次|這場對戰.*1次",
        "active_spot_mentioned": r"Active Spot|战斗场|戰鬥場",
        "shared_usage_limit_text": r"can.t use more than|同名特性|这个特性.*只可",
    }
    hints = sorted(k for k, p in patterns.items() if re.search(p, text, re.I))
    return {
        "operationFamilies": unit["families"],
        "lifecycleHints": hints or ["manual_trigger_review"],
    }


def build(catalog, release, articles):
    inventory = scope_inventory(catalog, release)
    implementations = implemented_effects(release, inventory["targets"])
    by_effect = {e["effectKey"]: e for e in implementations}
    pages = defaultdict(list)
    for card in inventory["targets"]:
        pages[card["cardPage"] or "identity:" + card["printingId"]].append(card)
    records, exact, parameter = [], defaultdict(list), defaultdict(list)
    ability_groups = {}
    for title, cards in sorted(pages.items()):
        source = articles.get(title, {})
        units, gaps = extract(source)
        if not source.get("revision"):
            gaps.append("REVISION_MISSING")
        remaining = [c["printingId"] for c in cards if not c["supported"]]
        supported = [c["printingId"] for c in cards if c["supported"]]
        effect_keys = sorted({c["effectKey"] for c in cards if c["supported"]})
        for u in units:
            if u["kind"] == "card_context":
                continue
            member = {
                "page": title,
                "unit": u["index"],
                "supportedPrintings": supported,
                "unsupportedPrintings": remaining,
                "implementedEffectKeys": effect_keys,
            }
            exact[u["exactGroup"]].append(member)
            parameter[u["parameterGroup"]].append(member)
            if u["kind"] == "ability":
                shape = ability_bucket(u)
                key = digest(shape)
                group = ability_groups.setdefault(
                    key, {"id": key, **shape, "members": [], "autoApproval": False}
                )
                group["members"].append(member)
        source_families = sorted({f for u in units for f in u["families"]})
        implementation_families = sorted(
            {f for key in effect_keys for f in by_effect[key]["families"]}
        )
        families = sorted(set(source_families) | set(implementation_families))
        if not families:
            families = ["unclassified"]
        records.append(
            {
                "page": title,
                "revision": source.get("revision"),
                "sourceHash": digest(source.get("text") or ""),
                "printings": [c["printingId"] for c in cards],
                "unsupportedPrintings": remaining,
                "supportedPrintings": supported,
                "implementedEffectKeys": effect_keys,
                "sourceFamilies": source_families,
                "implementationFamilies": implementation_families,
                "families": families,
                "units": units,
                "reviewBlockers": sorted(
                    set(
                        gaps
                        + [
                            g
                            for c in cards
                            for g in c["gaps"]
                            if g
                            not in ("EFFECT_UNSUPPORTED", "PRINTING_REVIEW_REQUIRED")
                        ]
                    )
                ),
                "status": "implemented"
                if not remaining
                else "review-and-implementation-required",
            }
        )
    families = []
    for key, label in [(k, v[0]) for k, v in FAMILIES.items()] + [
        ("unclassified", "待人工分类")
    ]:
        implemented = [e for e in implementations if key in e["families"]]
        rows = [
            r for r in records if key in r["families"] and r["unsupportedPrintings"]
        ]
        families.append(
            {
                "id": key,
                "label": label,
                "pages": len(rows),
                "printings": len({p for r in rows for p in r["unsupportedPrintings"]}),
                "abilityPages": sum(
                    any(u["kind"] == "ability" for u in r["units"]) for r in rows
                ),
                "implementedEffects": len(implemented),
                "supportedPrintings": len(
                    {p for e in implemented for p in e["supportedPrintings"]}
                ),
                "implementedEffectKeys": [e["effectKey"] for e in implemented],
                "coDependencies": dict(
                    Counter(f for r in rows for f in r["families"] if f != key)
                ),
                "pageRefs": [r["page"] for r in rows],
            }
        )
    families.sort(key=lambda f: (-f["printings"], f["id"]))

    # Groups are review candidates, never semantic equivalence or card admission.
    def groups(items):
        return [
            {"id": key, "members": value}
            for key, value in sorted(items.items())
            if len(value) > 1
        ]

    return {
        "schema": "p4-mechanism-backlog-v2",
        "catalogVersion": catalog["version"],
        "scopeVersion": inventory["scopeVersion"],
        "denominatorVersion": inventory["denominatorVersion"],
        "autoApproval": False,
        "limits": [
            "Families overlap: their printing counts must not be summed.",
            "Numeric similarity never proves equivalent effects.",
            "Ability timing, usage limits, targets, costs and suppression require explicit contracts.",
            "Multi-version trainer/energy pages require printing-to-rule-version review.",
            "Existing support status comes only from the published release, not this inventory.",
            "Implementation references are reuse candidates, not proof that similar pending cards already work.",
        ],
        "counts": {
            **inventory["counts"],
            "pageRecords": len(records),
            "unsupportedPages": sum(
                bool(r["unsupportedPrintings"])
                and not r["page"].startswith("identity:")
                for r in records
            ),
            "unsupportedWithoutPage": sum(
                len(r["unsupportedPrintings"])
                for r in records
                if r["page"].startswith("identity:")
            ),
            "abilityPages": sum(
                bool(r["unsupportedPrintings"])
                and any(u["kind"] == "ability" for u in r["units"])
                for r in records
            ),
            "exactRepeatedGroups": len(groups(exact)),
            "numericVariantGroups": len(groups(parameter)),
            "abilityReviewBuckets": len(ability_groups),
            "implementedEffects": len(implementations),
            "classifiedSupportedPrintings": len(
                {p for e in implementations for p in e["supportedPrintings"]}
            ),
            "unmappedImplementedMechanisms": sorted(
                {m for e in implementations for m in e["unmappedMechanisms"]}
            ),
        },
        "families": families,
        "pages": records,
        "implementedEffects": implementations,
        "exactGroups": groups(exact),
        "parameterGroups": groups(parameter),
        "abilityBuckets": sorted(
            ability_groups.values(), key=lambda g: (-len(g["members"]), g["id"])
        ),
        "unresolvedIdentities": inventory["unresolved"],
    }


def markdown(report):
    c = report["counts"]
    lines = [
        "# P4 机制族盘点",
        "",
        f"目标 {c['targets']}；已支持 {c['supported']}；未支持 {c['unsupported']}；涉及未闭环规则页 {c['unsupportedPages']}。",
        "",
        f"含特性的未闭环页面 {c['abilityPages']}；重复规则文本组 {c['exactRepeatedGroups']}；数值变体候选组 {c['numericVariantGroups']}。",
        "",
        "统计按多标签分类，不能将各行数量相加；相似组只用于人工审核，不自动放行卡牌。",
        f"已发布 {c['implementedEffects']} 种效果全部纳入实现索引，覆盖 {c['classifiedSupportedPrintings']} 个已支持版本；实现分类来自发布清单，待实现分类来自规则文本，两者均保留依据。",
        "",
        "| 机制族 | 已实现效果 | 已支持版本 | 未闭环页面 | 关联未支持印刷 | 含特性页面 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    lines += [
        f"| {f['label']} | {f['implementedEffects']} | {f['supportedPrintings']} | {f['pages']} | {f['printings']} | {f['abilityPages']} |"
        for f in report["families"]
    ]
    lines += [
        "",
        "完整页面、原始规则字段、修订哈希、分组成员和跨机制依赖见 `mechanism-backlog.json`。",
        "无标记身份仍单独保留；多版本训练家/能量页面和未知模板必须先审核，不能据当前页面首段规则自动发布全部版本。",
        "",
        "## 已发布实现索引",
        "",
        "包含发布清单中的所有效果；保留原有验证范围和交互审计状态，不因归类提升支持等级。缺少规则页的基本能量按已发布机制归类，来源缺口仍保留。",
        "",
        "| 效果 | 机制族 | 已支持版本 | 实现 | 测试目录 |",
        "|---|---|---:|---|---|",
    ]
    lines += [
        f"| {e['effectKey']} | {', '.join(e['families'])} | {len(e['supportedPrintings'])} | `{e['implementation']}` | `{e['evidenceSuite']}` |"
        for e in report["implementedEffects"]
    ]
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    report = build(
        read(ROOT / "data/catalog/catalog.json"),
        read(ROOT / "data/simulation/effects.json"),
        load_articles(),
    )
    out = ROOT / "artifacts/cardpool"
    (out / "mechanism-backlog.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )
    (out / "mechanism-backlog.md").write_text(markdown(report), encoding="utf8")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
