"""Closed deterministic adaptation; unsupported rules remain explicit tasks."""
from __future__ import annotations

from collections import defaultdict, Counter
import copy
import re

from .common import digest

VERSION = "chs-effect-adapter-1"


def environment_status(card, rules, scope, as_of):
    if not card.get("releasedAt"):
        return "unknown-release"
    if card["releasedAt"] > as_of:
        return "not-released"
    basic = card.get("basicEnergyType") in scope["basicEnergyTypes"]
    if not basic and card.get("mark") not in scope["allowedMarks"]:
        return "outside-scope" if card.get("mark") else "unknown-mark"
    if not basic and card.get("mark") not in rules["allowedMarks"]:
        return "outside-format"
    if card["printingId"] in rules.get("bannedPrintings", []):
        return "banned"
    return "eligible"


def compile_basic(face):
    """Only ordinary basic Pokémon with complete printed costs and numeric attacks.

    One additional deterministic family is supported: damage then self healing.
    All other text is rejected; no regex-based removal of unrecognized clauses.
    """
    if face["category"] != "宝可梦" or face["stage"] != "BASIC" or face["abilities"] or face["skills"] or face["ruleText"] or face["pokemonType"] or face["specialCard"]:
        return None
    if not isinstance(face["hp"], int) or face["hp"] <= 0 or not isinstance(face["retreat"], int) or face["retreat"] < 0 or not face["type"] or not face["attacks"]:
        return None
    if face["weakness"] and face["weaknessFormula"] != "×2":
        return None
    if face["resistance"] and face["resistanceFormula"] != "-30":
        return None
    attacks = []
    for a in face["attacks"]:
        if a.get("additionalEnergyCondition"):
            return None
        if "UNKNOWN" in a["cost"] or not re.fullmatch(r"\d*", a["damage"]):
            return None
        attack = {"name": a["name"], "cnName": a["name"], "cost": a["cost"], "damage": int(a["damage"] or "0"), "text": a["text"]}
        if a["text"]:
            heal = re.fullmatch(r"将这只宝可梦恢复([1-9]\d*)点HP。", a["text"])
            recovery = re.fullmatch(r"回复这只宝可梦「([1-9]\d*)」HP，并恢复其所有特殊状态。", a["text"])
            cure = a["text"] == "恢复这只宝可梦的全部特殊状态。"
            if not heal and not recovery and not cure:
                return None
            attack["mechanic"] = {"kind": "sync_self_recovery", "amount": int((heal or recovery)[1]) if heal or recovery else 0, "cure": bool(recovery or cure)}
        attacks.append(attack)
    key = "P4P-SYNC" + digest(face)[:16].upper()
    return {"effectKey": key, "name": face["name"], "hp": face["hp"], "type": face["type"], "stage": "BASIC", "evolvesFrom": [], "weakness": [face["weakness"]] if face["weakness"] else [], "resistance": [face["resistance"]] if face["resistance"] else [], "retreat": face["retreat"], "attacks": attacks, "printings": [], "sourceAdapter": VERSION}


def adapt(migration, normalized, effect_release, plain, rules, scope, as_of, previous=None, implementations=None):
    catalog = migration["catalog"]
    catalog["asOf"] = as_of
    cards = {c["printingId"]: c for c in catalog["cards"]}
    effects = copy.deepcopy(effect_release)
    specs = copy.deepcopy(plain)
    known = {e["effectKey"]: e for e in effects["effects"]}
    source_compiled = {s["effectKey"] for s in specs["cards"] if s.get("sourceAdapter") == VERSION}
    signatures = defaultdict(set)
    for cid, mapping in (previous or {}).get("mappings", {}).items():
        c = cards.get(mapping["printingId"], {})
        # Legacy identity matching preserves its existing support, but does not
        # prove that newly fetched text equals that implementation. Reuse only
        # source-compiled effects already admitted by a prior release.
        if c.get("effectStatus") == "verified" and c.get("engineId") in source_compiled:
            signatures[mapping["ruleHash"]].add(c["engineId"])
    tasks, proposals, rows = {}, {}, []
    for cid, mapping in migration["mappings"].items():
        c = cards[mapping["printingId"]]
        up = normalized["cards"][cid]
        implementation = (implementations or {}).get(cid)
        reviewed = bool(implementation and implementation["ruleHash"] == up["ruleHash"] and implementation["effectKey"] in known and not up["issues"] and mapping["status"] == "matched")
        face = up["face"]
        energy = {"GRASS":"grass", "FIRE":"fire", "WATER":"water", "LIGHTNING":"lightning", "PSYCHIC":"psychic", "FIGHTING":"fighting", "DARK":"darkness", "METAL":"metal"}.get(face.get("type")) if face.get("energyType") == "基本能量" else None
        facts = {"category":face["category"], "hp":face["hp"], "pokemonType":face["type"], "stage":face["stage"], "isBasicPokemon":face["stage"] == "BASIC" if face["stage"] else None, "subtype":face["trainerType"] or face["pokemonType"], "attacks":face["attacks"], "mark":up["mark"], "basicEnergyType":energy, "aceSpec":"ACE SPEC" in (face.get("specialCard") or "").split("|")}
        env = environment_status({**c, **facts} if reviewed else c, rules, scope, as_of)
        status, reason = "outside-environment", env
        if env == "eligible":
            if reviewed:
                status, reason = "implementation-candidate", "完整卡面复用已核验，字段和效果一并等待发布验证"
                proposals[c["printingId"]] = {"effectKey":implementation["effectKey"], "path":"C", "cardId":cid, "engineLine":implementation["engineLine"], "testFiles":implementation["testFiles"], "reviewedFacts":facts}
            elif c.get("effectStatus") == "verified" and c.get("engineId") in known:
                status, reason = "supported-existing", "保留已审核本地实现；新上游原文单独留作核对候选"
            elif up["issues"] or mapping["status"] != "matched":
                status, reason = "source-review", ",".join(up["issues"]) or "身份需要核对"
            else:
                keys = signatures.get(up["ruleHash"], set())
                implementation = (implementations or {}).get(cid)
                if implementation and implementation["ruleHash"] == up["ruleHash"] and implementation["effectKey"] in known:
                    key = implementation["effectKey"]
                    status, reason = "implementation-candidate", "本地机制实现与规则指纹匹配，等待版本绑定验收"
                    proposals[c["printingId"]] = {"effectKey": key, "path": "C", "cardId": cid, "engineLine": implementation["engineLine"], "testFiles": implementation["testFiles"]}
                elif len(keys) == 1:
                    key = next(iter(keys))
                    status, reason = "reuse-candidate", "完整规则指纹及审核身份匹配，等待发布验证"
                    proposals[c["printingId"]] = {"effectKey": key, "path": "A", "cardId": cid}
                else:
                    spec = compile_basic(up["face"])
                    if spec:
                        key = spec["effectKey"]
                        proposals[c["printingId"]] = {"effectKey": key, "path": "B", "cardId": cid, "spec": spec}
                        status, reason = "compiled-candidate", "受限规则编译通过，等待机制、AI 和发布验证"
                    else:
                        status, reason = "implementation-required", "规则超出当前确定性编译范围"
                if status == "implementation-required":
                    task_id = digest(up["face"])
                    task = tasks.setdefault(task_id, {"id": task_id, "state": "identified", "mechanism": "unparsed-card-face", "face": up["face"], "sourceHash": up["sourceHash"], "cardIds": [], "printingIds": [], "environment": rules["id"], "requirements": ["核对完整规则", "实现费用、目标、结算和失败行为", "增加机制边界和旧卡交互测试", "验证 A1 合法决策及隐藏信息", "通过版本绑定的发布验收"]})
                    task["cardIds"].append(cid)
                    task["printingIds"].append(c["printingId"])
        rows.append({"cardId": cid, "printingId": c["printingId"], "name": up["name"], "environment": env, "status": status, "reason": reason})
    catalog["version"] = digest({k: v for k, v in catalog.items() if k != "version"})
    return {"rows": rows, "tasks": list(tasks.values()), "proposals": proposals, "statuses": dict(Counter(r["status"] for r in rows)), "effects": effects, "plain": specs, "environment": {"rules": rules, "scope": scope, "asOf": as_of}}


def enable_proposals(catalog, result, name_bindings=()):
    """Build an isolated candidate, never mutate the currently published catalog."""
    catalog, effects, plain = copy.deepcopy(catalog), copy.deepcopy(result["effects"]), copy.deepcopy(result["plain"])
    cards = {c["printingId"]: c for c in catalog["cards"]}
    keys = {s["effectKey"]: s for s in plain["cards"]}
    registry = {e["effectKey"]: e for e in effects["effects"]}
    engine_lines = {c["engineId"]: c.get("engineLine") for c in catalog["cards"] if c.get("engineId") and c.get("engineLine")}
    for pid, proposal in result["proposals"].items():
        key = proposal["effectKey"]
        if "spec" in proposal:
            if key not in keys:
                keys[key] = copy.deepcopy(proposal["spec"])
                plain["cards"].append(keys[key])
            keys[key]["printings"] = sorted(set(keys[key]["printings"]) | {pid})
            registry.setdefault(key, {"effectKey": key, "name": keys[key]["name"], "printings": [], "status": "experimental", "registered": True, "mechanisms": ["attack_damage"] + sorted({a["mechanic"]["kind"] for a in keys[key]["attacks"] if "mechanic" in a}), "implementation": "packages.rules.plain.PlainPokemon", "rulesRevision": result["environment"]["asOf"], "scope": "sync-closed-grammar"})
        registry[key]["printings"] = sorted(set(registry[key]["printings"]) | {pid})
        name = registry[key]["name"]
        c = cards[pid]
        c.update(proposal.get("reviewedFacts", {}))
        c.update(engineId=key, definitionId=key, effectStatus="verified", sourceVerified=True, effectEvidence=c["sourceEvidence"], engineLine=proposal.get("engineLine") or (f"{name} P4P {key.split('-', 1)[1]}" if key.startswith("P4P-") else engine_lines.get(key)), reviewNote="同步候选效果；仅在版本绑定验收通过后发布。")
    for binding in name_bindings:
        key = binding["effectKey"]
        spec = keys.get(key)
        if spec is None or key not in registry:
            continue
        assert spec.get("sourceAdapter") == VERSION
        assert spec["name"] in (binding["printedName"], binding["engineName"])
        spec["name"] = registry[key]["name"] = binding["engineName"]
        spec["cnName"] = binding["printedName"]
        spec["nameBindingEvidence"] = binding
        for card in cards.values():
            if card.get("engineId") == key:
                card["englishName"] = binding["engineName"]
                card["engineLine"] = f"{binding['engineName']} P4P {key.split('-', 1)[1]}"
    # A reviewed face may replace an older implementation for the same printing.
    # Keep the old implementation available to frozen matches, but do not leave
    # a second live alias pointing at a different effect than the catalogue.
    for row in [*registry.values(), *plain["cards"]]:
        row["printings"] = sorted({pid for pid in row["printings"]
                                  if pid in cards and cards[pid].get("engineId") == row["effectKey"]})
    for row in registry.values():
        if row.get("scope") in {"sync-closed-grammar", "reviewed-source-clauses", "source-face-review", "reviewed-source-wording"}:
            row.setdefault("testSuites", ["tests/simulation/test_prompt_matrix.py", "tests/sync/test_reviewed_support.py"])
    # A rejected/incomplete source face can leave an obsolete candidate record.
    # It is not an installed effect and must not be advertised as registered.
    for key in list(registry):
        if key.startswith("P4P-") and key not in keys and registry[key].get("scope") in {"sync-closed-grammar", "reviewed-source-clauses"}:
            assert not registry[key]["printings"], f"MISSING_COMPILED_DEFINITION: {key}"
            del registry[key]
    effects["effects"] = list(registry.values())
    catalog["version"] = digest({k: v for k, v in catalog.items() if k != "version"})
    return catalog, effects, plain
