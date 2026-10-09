"""Conservative migration: existing IDs survive; unknown variants never split stock."""
from __future__ import annotations

from collections import defaultdict, Counter
import copy

from .common import REPOSITORY, digest
from .normalize import compact
from .finishes import refresh as refresh_finishes


def sections(face):
    result = [{"kind": "特性", **a, "locale": "zh-Hans"} for a in face["abilities"]]
    result += [{"kind": "招式", **a, "locale": "zh-Hans"} for a in face["attacks"]]
    for skill in face["skills"]:
        result.append({"kind": "特性" if skill.get("type") == "feature" else "招式", "name": skill.get("name", ""), "text": skill.get("desc", ""), "locale": "zh-Hans", "special": skill.get("related")})
    if face["ruleText"]:
        result.append({"kind": "规则", "name": "卡牌规则", "text": face["ruleText"], "locale": "zh-Hans"})
    return result


def build_catalog(normalized, baseline, detail_baseline, commit, mappings=None, overrides=None, previous=None, *, repository=REPOSITORY):
    cards = {c["printingId"]: copy.deepcopy(c) for c in baseline["cards"]}
    details = copy.deepcopy(detail_baseline)
    details.setdefault("cards", {})
    fixed = (mappings or {}).get("cards", {})
    prior = (previous or {}).get("mappings", {})
    indexed = defaultdict(set)
    for card in cards.values():
        indexed[(compact(card.get("productCode")), compact(card.get("collectorNumber")), compact(card.get("cnName")))].add(card["printingId"])
    occurrences = defaultdict(list)
    for row in normalized["occurrences"]:
        occurrences[row["cardId"]].append(row)
    source_url = f"{repository}/blob/{commit}/ptcg_chs_infos.json"
    output_mapping, conflicts, grouped = {}, [], {}
    added = set()
    for cid, up in normalized["cards"].items():
        face = up["face"]
        existing = fixed.get(cid, {}).get("printingId") or prior.get(cid, {}).get("printingId")
        candidates = set()
        for row in occurrences[cid]:
            candidates.update(indexed.get((compact(row["productCode"]), compact(up["number"]).split("/")[0], compact(up["name"])), set()))
        if existing and existing not in cards:
            conflicts.append({"cardId": cid, "code": "MAPPING_TARGET_MISSING", "printingId": existing})
            existing = None
        if not existing and len(candidates) == 1:
            candidate = cards[next(iter(candidates))]
            number = str(candidate.get("printedNumber") or candidate.get("numberLabel") or "")
            if (not number or "/" not in number or number == up["number"]) and (
                not candidate.get("hp") or not face["hp"] or candidate["hp"] == face["hp"]
            ):
                existing = candidate["printingId"]
        if not existing and candidates:
            conflicts.append({"cardId": cid, "code": "IDENTITY_REVIEW", "candidates": sorted(candidates)})
        # Same numbered face/commodity can have several finishes. Keep each variant,
        # group only exact complete faces, and preserve all product occurrences.
        group = digest({"number": up["number"], "face": up["ruleHash"], "code": occurrences[cid][0]["productCode"]})
        pid = existing or grouped.get(group) or "CN:CHS:" + cid
        grouped.setdefault(group, pid)
        product_ids = [normalized["products"][r["productId"]]["id"] for r in occurrences[cid]]
        dates = [normalized["products"][r["productId"]]["releasedAt"] for r in occurrences[cid] if normalized["products"][r["productId"]]["releasedAt"]]
        if pid not in cards:
            added.add(pid)
            first = normalized["products"][occurrences[cid][0]["productId"]]
            cards[pid] = {"printingId": pid, "identityKind": "numbered-printing" if up["number"] else "unnumbered", "collectorNumber": up["number"].split("/")[0], "printedNumber": up["number"], "productCode": first["code"], "productName": first["name"], "cnName": up["name"], "englishName": "", "region": "CN", "language": "zh-Hans", "releasedAt": min(dates) if dates else None, "mark": up["mark"], "nameLimitKey": up["name"], "isBasicPokemon": face["stage"] == "BASIC" if face["stage"] else None, "aceSpec": face["specialCard"] == "ACE SPEC", "basicEnergyType": None, "sourceVerified": False, "sourceEvidence": [source_url], "effectStatus": "unverified", "engineId": "unverified:" + pid, "definitionId": "unverified:" + pid, "reviewNote": "上游资料已同步；规则支持由独立验收决定。", "images": [], "category": face["category"], "subtype": face["trainerType"] or face["pokemonType"], "pokemonType": face["type"], "hp": face["hp"], "stage": face["stage"], "evolvesFrom": [], "attacks": face["attacks"], "aliases": [up["name"]], "productIds": [], "rarity": up["rarity"], "catalogStatus": "upstream-listed"}
        card = cards[pid]
        card["productIds"] = sorted(set(card.get("productIds", [])) | set(product_ids))
        card["variants"] = [v for v in card.get("variants", []) if v["upstreamId"] != cid]
        card["variants"].append({"variantId": "chs-variant:" + cid, "upstreamId": cid, "rarity": up["rarity"], "finishCode": up["finishCode"], "finishLabel": "工艺待核验" if up["finishCode"] else "未指定特殊工艺", "imagePath": up["imagePath"], "commit": commit, "productIds": product_ids})
        if up.get('finishResolution'):
            card['variants'][-1]['finishResolution'] = up['finishResolution']
        if up["imagePath"] and up.get("imageBlob") and not {"IDENTITY_CONFLICT", "DUPLICATE_CARD_CONFLICT"}.intersection(up["issues"]):
            image = {"url": f"/api/sync/images/{commit}/{cid}", "locale": "zh-Hans", "source": f"{repository}/blob/{commit}/{up['imagePath']}", "equivalenceReviewed": True, "matchMethod": "source-record-product-number-name", "variantId": "chs-variant:" + cid}
            card["variants"][-1]["image"] = image
            # Choose one deterministic version; expose all variants separately.
            if not any(i.get("matchMethod") == "source-record-product-number-name" for i in card["images"]):
                card["images"].insert(0, image)
            else:
                for i, old_image in enumerate(card["images"]):
                    if old_image.get("variantId") == image["variantId"]:
                        card["images"][i] = image
        card.setdefault("upstreamFaces", {})[cid] = {"ruleHash": up["ruleHash"], "sourceHash": up["sourceHash"], "commit": commit}
        if face["energyType"] == "基本能量":
            card["basicEnergyType"] = {"GRASS": "grass", "FIRE": "fire", "WATER": "water", "LIGHTNING": "lightning", "PSYCHIC": "psychic", "FIGHTING": "fighting", "DARK": "darkness", "METAL": "metal", "FAIRY": "fairy"}.get(face["type"])
        card["aceSpec"] = "ACE SPEC" in (face["specialCard"] or "").split("|")
        if not card.get("mark"):
            card["mark"] = up["mark"]
        if not card.get("rarity"):
            card["rarity"] = up["rarity"]
        card["upstreamRemoved"] = False
        old_mapping = prior.get(cid)
        rule_changed = old_mapping and old_mapping["ruleHash"] != up["ruleHash"]
        if rule_changed:
            card.update(effectStatus="unverified", sourceVerified=False, reviewNote="上游规则已变更，需重新验证。")
        # Source-owned records must follow revisions as well as first import.
        # Keep reviewed legacy facts until their source change is revalidated.
        if pid.startswith("CN:CHS:") or rule_changed:
            card.update(hp=face["hp"], pokemonType=face["type"], stage=face["stage"], isBasicPokemon=face["stage"] == "BASIC" if face["stage"] else None, attacks=face["attacks"], category=face["category"], subtype=face["trainerType"] or face["pokemonType"], mark=up["mark"], releasedAt=min(dates) if dates else None, rarity=up["rarity"])
        # Existing reviewed text stays authoritative until its equivalence is checked.
        if pid in added or card.get("effectStatus") != "verified":
            details["cards"][pid] = {"sections": sections(face), "source": source_url, "textStatus": "upstream-available", "imageGap": "上游卡图待下载", "upstreamCardId": cid}
        else:
            details["cards"].setdefault(pid, {})["upstreamCandidate"] = {"source": source_url, "cardId": cid, "sections": sections(face)}
        output_mapping[cid] = {"printingId": pid, "variantId": "chs-variant:" + cid, "ruleHash": up["ruleHash"], "sourceHash": up["sourceHash"], "method": "reviewed" if cid in fixed else "retained" if cid in prior else "identity-candidate" if existing else "new", "status": "pending" if up["issues"] or (not existing and candidates) else "matched"}
    active_pids = {m["printingId"] for m in output_mapping.values()}
    for cid, mapping in prior.items():
        if cid not in output_mapping and mapping["printingId"] in cards:
            card = cards[mapping["printingId"]]
            for variant in card.get("variants", []):
                if variant["upstreamId"] == cid:
                    variant["removed"] = True
            if mapping["printingId"] not in active_pids:
                card["upstreamRemoved"] = True
                card["effectStatus"] = "unverified"
    for pid, patch in (overrides or {}).get("cards", {}).items():
        if pid not in cards or patch.get("baseHash") != digest(cards[pid]):
            conflicts.append({"printingId": pid, "code": "OVERRIDE_STALE"})
            continue
        # Never accept support flags from a display override.
        allowed = {"cnName", "aliases", "evolvesFrom", "reviewNote"}
        if set(patch.get("set", {})) - allowed:
            conflicts.append({"printingId": pid, "code": "OVERRIDE_FIELD_FORBIDDEN"})
            continue
        cards[pid].update(patch["set"])
    for card in cards.values():
        refresh_finishes(card)
    products = {p["id"]: p for p in baseline.get("products", [])}
    products.update({p["id"]: {"id": p["id"], "name": p["name"], "title": p["name"], "releasedAt": p["releasedAt"], "status": "released", "upstreamCode": p["code"], "series": p["series"], "source": {"url": source_url}} for p in normalized["products"].values()})
    catalog = {**copy.deepcopy(baseline), "cards": list(cards.values()), "products": list(products.values()), "upstream": {"repository": repository, "commit": commit}}
    catalog["version"] = digest({k: v for k, v in catalog.items() if k != "version"})
    return {"catalog": catalog, "details": details, "mappings": output_mapping, "conflicts": conflicts, "report": {"before": len(baseline["cards"]), "after": len(cards), "added": len(added), "matched": len(output_mapping), "conflicts": len(conflicts), "variants": len(normalized["cards"]), "mappingMethods": dict(Counter(m["method"] for m in output_mapping.values()))}}
