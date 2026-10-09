"""Read-only coverage audit of both the active release and workspace rules."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def audit(base):
    catalog = read(base / "data/catalog/catalog.json")
    policy = read(base / "rulesets/cn-standard-2026-09-16.json")
    scope = read(base / "data/cardpool/battle-scope.json")
    effects = {e["effectKey"]: e for e in read(base / "data/simulation/effects.json")["effects"]}
    source = read(ROOT / ".catalog/sync/snapshots" / catalog["upstream"]["commit"] / "normalized.json")["cards"]
    counts, reasons = Counter(), Counter()
    gaps, uncertain, supported = [], [], []
    marks = set(policy["allowedMarks"]) & set(scope["allowedMarks"])
    for c in catalog["cards"]:
        originals = [source[cid] for cid in c.get("upstreamFaces", {}) if cid in source]
        source_marks = {s.get("mark") for s in originals} | {c.get("mark")}
        basic = c.get("basicEnergyType") in scope["basicEnergyTypes"]
        source_basic = any(s["face"]["category"] == "能量" and s["face"].get("energyType") == "基本能量" and s["face"].get("type") in {"GRASS", "FIRE", "WATER", "LIGHTNING", "PSYCHIC", "FIGHTING", "DARK", "METAL"} for s in originals)
        row = {"printingId": c["printingId"], "name": c["cnName"], "product": c.get("productCode"), "mark": c.get("mark"), "effectKey": c.get("engineId"), "ruleHashes": sorted({s["ruleHash"] for s in originals})}
        if not c.get("releasedAt"):
            counts["dateUncertain"] += 1
            uncertain.append({**row, "reason": "发售日期未确认"})
            continue
        if c["releasedAt"] > catalog["asOf"]:
            counts["unreleased"] += 1
            continue
        if c["printingId"] in policy.get("bannedPrintings", []):
            counts["banned"] += 1
            continue
        if not (source_marks & marks or basic or source_basic):
            if any(s["face"]["category"] == "能量" and s["face"].get("energyType") == "基本能量" for s in originals):
                counts["outside"] += 1
            elif not source_marks - {None, ""}:
                counts["markUncertain"] += 1
                uncertain.append({**row, "reason": "监管标记未确认"})
            else:
                counts["outside"] += 1
            continue
        counts["target"] += 1
        blockers = []
        effect = effects.get(c.get("engineId"))
        if not c.get("sourceVerified"): blockers.append("来源身份未核验")
        if c.get("effectStatus") != "verified": blockers.append("效果未核验")
        if not effect: blockers.append("缺少效果映射")
        elif effect.get("status") != "experimental" or c["printingId"] not in effect["printings"]:
            blockers.append("效果发布未接纳此印刷")
        if source_basic and not basic: blockers.append("基本能量映射缺失")
        if not basic and c.get("mark") not in marks: blockers.append("本地监管标记不匹配")
        if c.get("category") == "宝可梦" and c.get("isBasicPokemon") is None: blockers.append("进化阶段未确认")
        if blockers:
            gaps.append({**row, "blockers": blockers})
            reasons.update(blockers)
        else:
            supported.append(row)
    hashes = {}
    for file in ("data/catalog/catalog.json", "data/simulation/effects.json", "data/cardpool/battle-scope.json", "rulesets/cn-standard-2026-09-16.json"):
        hashes[file] = hashlib.sha256((base / file).read_bytes()).hexdigest()
    assert counts["target"] == len(gaps) + len(supported)
    return {"asOf": catalog["asOf"], "format": policy["id"], "catalogCards": len(catalog["cards"]), "counts": dict(counts), "supported": len(supported), "supportedEffects": len({r["effectKey"] for r in supported}), "unsupported": len(gaps), "unsupportedRuleHashes": len({h for r in gaps for h in r["ruleHashes"]}), "reasons": dict(reasons), "inputHashes": hashes, "gaps": gaps, "uncertain": uncertain}


if __name__ == "__main__":
    pointer = read(ROOT / ".catalog/sync/current.json")
    result = {"checkedAt": datetime.now(timezone.utc).isoformat(), "releaseId": pointer["releaseId"], "active": audit(ROOT / ".catalog/sync/releases" / pointer["releaseId"]), "workspace": audit(ROOT)}
    result["releaseUnchanged"] = pointer == read(ROOT / ".catalog/sync/current.json")
    output = ROOT / "artifacts/sync/current-rules-coverage.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**result, **{k: {n: v for n, v in result[k].items() if n not in ("gaps", "uncertain", "inputHashes")} for k in ("active", "workspace")}}, ensure_ascii=False, indent=2))
