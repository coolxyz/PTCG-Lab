"""Source-independent card faces, preserving unknowns and original identities."""
from __future__ import annotations

from datetime import date
from collections import defaultdict
import re

from .common import SyncError, digest
from .source import GitSource

VERSION = "chs-normalizer-3"
ELEMENTS = {"草": "GRASS", "火": "FIRE", "水": "WATER", "雷": "LIGHTNING", "超": "PSYCHIC", "斗": "FIGHTING", "恶": "DARK", "钢": "METAL", "妖": "FAIRY", "龙": "DRAGON", "无色": "COLORLESS"}
STAGES = {"基础": "BASIC", "1阶进化": "STAGE_1", "2阶进化": "STAGE_2", "V进化": "V_EVOLUTION"}
COSMETIC = {"id", "collectionNumber", "commodityCode", "commodityList", "collectionFlag", "special_shiny_type", "rarity", "rarityText", "illustratorName", "pokedexText", "pokedexCode", "pokemonCategory", "height", "weight", "anniversaryCode", "yorenCode", "regulationMark", "regulationMarkText"}
KNOWN_RULE_FIELDS = {"cardName", "cardType", "cardTypeText", "hp", "attribute", "evolveText", "abilityItemList", "cardFeatureItemList", "skills", "ruleText", "retreatCost", "weaknessType", "weaknessFormula", "resistanceType", "resistanceFormula", "pokemonType", "trainerType", "trainerTypeText", "energyType", "energyTypeText", "specialCard", "featureFlag"}


def source_text(value):
    # This upstream uses the literal string "none" for blank attack text/damage.
    # Decode only this exact sentinel, retaining the original value in raw.
    return "" if value is None or isinstance(value, str) and value.lower() == "none" else str(value)


def compact(value):
    return re.sub(r"\s+", "", str(value or "")).casefold()


def normalize(raw, tree=None, corrections=None):
    if not isinstance(raw, dict) or not isinstance(raw.get("dict"), dict) or not isinstance(raw.get("collections"), list):
        raise SyncError("SOURCE_SCHEMA", "上游缺少 dict 或 collections")
    if not raw["collections"]:
        raise SyncError("EMPTY_SOURCE", "上游商品为空，拒绝作为有效快照")
    dictionaries = {}
    for name, rows in raw["dict"].items():
        if not isinstance(rows, list):
            raise SyncError("DICTIONARY_SCHEMA", "上游字典格式发生变化")
        dictionaries[name] = {str(r["dictCode"]): r["dictValue"] for r in rows}
    products, cards, occurrences, problems = {}, {}, [], []
    finish_sources = defaultdict(list)
    for product in raw["collections"]:
        if not all(k in product for k in ("id", "commodityCode", "name", "cards")) or not isinstance(product["cards"], list):
            raise SyncError("PRODUCT_SCHEMA", "上游商品结构不完整")
        pid = str(product["id"])
        if pid in products:
            raise SyncError("DUPLICATE_PRODUCT", "上游商品 ID 重复")
        release = product.get("salesDate")
        if release:
            try:
                date.fromisoformat(release)
            except (ValueError, TypeError):
                problems.append({"productId": pid, "code": "INVALID_RELEASE_DATE"})
                release = None
        products[pid] = {"id": "chs-product:" + pid, "upstreamId": pid, "code": product["commodityCode"], "name": product["name"].strip(), "releasedAt": release, "series": product.get("seriesText"), "goodsType": product.get("goodsType"), "imagePath": product.get("image"), "raw": {k: v for k, v in product.items() if k != "cards"}}
        for c in product["cards"]:
            d = c.get("details")
            if not isinstance(d, dict) or not all(k in d for k in ("id", "cardName", "collectionNumber", "cardType")):
                raise SyncError("CARD_SCHEMA", "卡牌缺少必需详情")
            cid = str(c["id"])
            original = d
            correction = (corrections or {}).get("cards", {}).get(cid)
            if correction:
                if correction.get("sourceHash") != digest(original):
                    raise SyncError("SOURCE_CORRECTION_STALE", f"卡牌 {cid} 的上游内容已变化，请重新核对本地修正")
                if not correction.get("evidence") or set(correction.get("set", {})) - {"pokemonType", "trainerType", "specialCard", "retreatCost"}:
                    raise SyncError("SOURCE_CORRECTION_INVALID", "来源修正必须有证据且仅修改已审核字段")
                d = {**original, **correction["set"]}
                if "retreatCost" in correction["set"] and (type(d["retreatCost"]) is not int or d["retreatCost"] < 0):
                    raise SyncError("SOURCE_CORRECTION_INVALID", "撤退费用必须是非负整数")
                if correction.get("attackTexts"):
                    d["abilityItemList"] = [dict(a) for a in original.get("abilityItemList", [])]
                    for replacement in correction["attackTexts"]:
                        index = replacement.get("index")
                        if (type(index) is not int or not 0 <= index < len(d["abilityItemList"])
                                or d["abilityItemList"][index].get("abilityText") != replacement.get("before")
                                or not isinstance(replacement.get("after"), str) or not replacement["after"]):
                            raise SyncError("SOURCE_CORRECTION_INVALID", "招式文字修正必须匹配原文并提供确认后的文字")
                        d["abilityItemList"][index]["abilityText"] = replacement["after"]
            issues = []
            if set(d) - COSMETIC - KNOWN_RULE_FIELDS:
                issues.append("UNKNOWN_RULE_FIELDS")
            if str(d["id"]) != cid or c.get("name") != d["cardName"]:
                issues.append("IDENTITY_CONFLICT")

            def decoded(group, value, required=False):
                if value is None or value == "":
                    if required:
                        issues.append("MISSING_" + group.upper())
                    return None
                separator = "|" if group == "pokemon_type" else "," if group == "special_card" else None
                if separator and separator in str(value):
                    values = [decoded(group, part.strip()) for part in str(value).split(separator)]
                    return "|".join(values) if all(values) else None
                result = dictionaries.get(group, {}).get(str(value))
                # The anniversary reprint retains the pre-subtype TRAINER face.
                if group == "trainer_type" and str(value) == "99":
                    result = "旧版训练家"
                if result is None:
                    issues.append("UNKNOWN_" + group.upper())
                return result

            def costs(value):
                if value in (None, ""):
                    return []
                if not isinstance(value, str):
                    issues.append("INVALID_ABILITY_COST")
                    return []
                return [ELEMENTS.get(decoded("ability_cost", part.strip()), "UNKNOWN") for part in value.split(",") if part.strip() not in ("none", "12")]

            category = decoded("card_type", d["cardType"], True)
            mark = d.get("regulationMarkText")
            if d.get("regulationMark") is not None:
                code_mark = decoded("regulation_mark", d["regulationMark"])
                if mark and code_mark != mark:
                    issues.append("MARK_CONFLICT")
                mark = mark or code_mark
            attacks = []
            for a in d.get("abilityItemList", []):
                if set(a) - {"abilityName", "abilityText", "abilityDamage", "abilityCost"}:
                    issues.append("UNKNOWN_ATTACK_FIELDS")
                attacks.append({"name": a.get("abilityName", ""), "text": source_text(a.get("abilityText")), "damage": source_text(a.get("abilityDamage")), "cost": costs(a.get("abilityCost"))})
                if "12" in str(a.get("abilityCost", "")).split(","):
                    # Printed '+' is an additional-energy condition, not an energy.
                    attacks[-1]["additionalEnergyCondition"] = True
            features = [{"name": a.get("featureName", ""), "text": a.get("featureDesc", "")} for a in d.get("cardFeatureItemList", [])]
            if any(set(a) - {"featureName", "featureDesc"} for a in d.get("cardFeatureItemList", [])):
                issues.append("UNKNOWN_FEATURE_FIELDS")
            face = {"name": d["cardName"], "category": category, "hp": d.get("hp"), "type": ELEMENTS.get(decoded("attribute", d.get("attribute"))), "stage": STAGES.get(d.get("evolveText")), "evolveText": d.get("evolveText"), "attacks": attacks, "abilities": features, "skills": d.get("skills", []), "ruleText": d.get("ruleText", ""), "retreat": d.get("retreatCost"), "weakness": ELEMENTS.get(decoded("weakness_type", d.get("weaknessType"))), "weaknessFormula": d.get("weaknessFormula"), "resistance": ELEMENTS.get(decoded("resistance_type", d.get("resistanceType"))), "resistanceFormula": d.get("resistanceFormula"), "pokemonType": decoded("pokemon_type", d.get("pokemonType")), "trainerType": decoded("trainer_type", d.get("trainerType")), "energyType": decoded("energy_type", d.get("energyType")), "specialCard": decoded("special_card", d.get("specialCard"))}
            if category == "宝可梦" and not attacks and not features and not face["skills"]:
                issues.append("MISSING_RULES")
            if category == "能量" and face["energyType"] == "基本能量" and not face["type"]:
                energy_name = re.fullmatch(r"基本([草火水雷超斗恶钢妖])能量", d["cardName"])
                if energy_name:
                    face["type"] = ELEMENTS[energy_name[1]]
            # Redundant, mandatory ex metadata is sometimes omitted in one
            # field. Reconcile only the explicit type or the exact ex rule,
            # never infer a rule box from a name suffix.
            ex_rule = "当宝可梦ex【昏厥】时，对手将拿取2张奖赏卡。"
            if category == "宝可梦":
                if face["pokemonType"] == "宝可梦ex" and not face["ruleText"]:
                    face["ruleText"] = ex_rule
                elif face["pokemonType"] is None and face["ruleText"] == ex_rule and not d.get("pokemonType"):
                    face["pokemonType"] = "宝可梦ex"
            image = c.get("image")
            if image:
                GitSource.validate_path(image)
            # Unknown fields stay in the semantic fingerprint instead of disappearing.
            semantic = {k: v for k, v in d.items() if k not in COSMETIC}
            record = {"id": cid, "number": str(d["collectionNumber"]), "name": d["cardName"], "mark": mark, "rarity": d.get("rarityText"), "finishCode": d.get("special_shiny_type"), "face": face, "semantic": semantic, "ruleHash": digest({"raw": semantic, "decoded": face}), "imagePath": image, "imageBlob": (tree or {}).get(image), "sourceHash": digest(original), "issues": issues, "raw": original}
            if correction:
                record["correction"] = correction
            if cid in cards and cards[cid]["ruleHash"] != record["ruleHash"]:
                problems.append({"cardId": cid, "code": "DUPLICATE_CARD_CONFLICT"})
                cards[cid]["issues"].append("DUPLICATE_CARD_CONFLICT")
            elif cid not in cards:
                cards[cid] = record
            occurrences.append({"cardId": cid, "productId": pid, "productCode": product["commodityCode"], "imagePath": image, "imageBlob": (tree or {}).get(image)})
            finish_sources[cid].append({"productId": pid, "productCode": product["commodityCode"], "declaredCode": d.get("commodityCode"), "finishCode": d.get("special_shiny_type"), "rarity": d.get("rarityText"), "imageBlob": (tree or {}).get(image)})
    for cid, sources in finish_sources.items():
        if len({(s['finishCode'], s['rarity']) for s in sources}) <= 1:
            continue
        # Bundles can reuse an expansion ID/image while resetting its finish.
        # Resolve only against its own printed expansion, with identical images.
        canonical = [s for s in sources if s['productCode'] == s['declaredCode']]
        blobs = {s['imageBlob'] for s in sources}
        values = {(s['finishCode'], s['rarity']) for s in canonical}
        resolved = len(values) == 1 and len(blobs) == 1 and None not in blobs
        cards[cid]['finishResolution'] = {'status': 'resolved' if resolved else 'needs-review', 'method': 'printed-expansion-identical-image', 'sources': sources}
        if resolved:
            cards[cid]['finishCode'], cards[cid]['rarity'] = next(iter(values))
        else:
            cards[cid]['finishCode'] = None
            problems.append({'cardId': cid, 'code': 'FINISH_CONFLICT'})
    return {"schema": VERSION, "dictionaryHash": digest(dictionaries), "products": products, "cards": cards, "occurrences": occurrences, "problems": problems}


def difference(previous, current):
    result = {}
    for kind in ("products", "cards"):
        old, new = (previous or {}).get(kind, {}), current[kind]
        result[kind] = {"added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()), "changed": sorted(k for k in new.keys() & old.keys() if digest(new[k]) != digest(old[k]))}
    old = (previous or {}).get("cards", {})
    result["ruleChanged"] = [k for k, v in current["cards"].items() if k in old and v["ruleHash"] != old[k]["ruleHash"]]
    result["imageChanged"] = [k for k, v in current["cards"].items() if k in old and (v["imageBlob"], v["imagePath"]) != (old[k].get("imageBlob"), old[k].get("imagePath"))]
    result["dictionaryChanged"] = bool(previous and previous["dictionaryHash"] != current["dictionaryHash"])
    result["relationshipsChanged"] = digest((previous or {}).get("occurrences", [])) != digest(current["occurrences"])
    return result
