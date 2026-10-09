"""Close the user-authorized implementation scope without certifying source gaps.

Directed execution covers every released effect. A1 is a stability sample;
browser tests cover shared interaction controls, not every possible card pair.
"""
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.cardpool.scope_inventory import build
from scripts.cardpool.reprints import read, encoded
from packages.battle.runtime import ENGINE_VERSION
from packages.battle.agent import VERSION as AI_VERSION
from packages.simulation.registry import VERSION as RELEASE_VERSION
from packages.cardpool.scope import VERSION as SCOPE_VERSION


def assess(inventory, effects, exceptions, cases, browser, matches):
    versions = {"engineVersion": ENGINE_VERSION, "releaseVersion": RELEASE_VERSION,
                "scopeVersion": SCOPE_VERSION, "catalogVersion": inventory["catalogVersion"], "aiVersion": AI_VERSION}
    failing = [c for c in cases if any(c.find(k) is not None for k in ("failure", "error", "skipped"))]
    directed = {m[1] for c in cases if (m := re.match(r"test_directed_pause_continuations\[([^\]]+)-(?:AttackAction|Use\w+Action|PutStadiumAction|PlayPokemonAction|EvolvePokemonAction|AttachEnergyAction|DiscardDoll)-", c.get("name", ""))) and c not in failing}
    effect_ids = {e["effectKey"] for e in effects["effects"]}
    targets = inventory["targets"]
    exempt = set(exceptions["printingIds"])
    unsupported = [c["printingId"] for c in targets if not c["supported"]]
    unexpected = sorted(set(unsupported) - exempt)
    rows = matches.get("results", [])
    stats = browser.get("stats", {})
    gates = {
        "allNonExceptionTargetsSupported": bool(targets) and not unexpected,
        "exceptionsRemainBlocked": exceptions["status"] == "pending-source-verification" and all(not c["supported"] for c in targets if c["printingId"] in exempt),
        "fullBackendRegression": bool(cases) and not failing,
        "everyReleasedEffectExecutedAndRestored": effect_ids <= directed,
        "sharedBrowserInteractions": stats.get("expected", 0) > 0 and not any(stats.get(k, 0) for k in ("unexpected", "flaky", "skipped")),
        "A1Stability": len(rows) >= read(ROOT / "data/cardpool/mechanism-workflow.json")["releaseGames"] and len(rows) == matches.get("games") and all(r["status"] == "finished" and not r.get("fallbacks") for r in rows),
        "versionBinding": all(matches.get(k) == v for k, v in versions.items()),
    }
    return {"schema": "p4-authorized-scope-acceptance-v1", "status": "PASS_WITH_SOURCE_EXCEPTIONS" if all(gates.values()) else "INCOMPLETE",
            **versions, "gates": gates, "counts": inventory["counts"],
            "backend": {"tests": len(cases), "failedOrSkipped": len(failing), "directedEffects": len(directed), "missingDirectedEffects": sorted(effect_ids - directed)},
            "browser": stats, "A1": {"games": len(rows), "statuses": matches.get("statuses"), "fallbacks": matches.get("fallbacks"), "observedEffects": len({k.split(':')[0] for k in matches.get('actionCounts', {})})},
            "unsupportedOutsideExceptions": unexpected, "sourceExceptions": exceptions,
            "evolutionPoolLimitations": matches.get("unavailableEvolutionChains", []),
            "unqualifiedFullP4Status": "INCOMPLETE",
            "limits": ["待核实例外不计入已支持；未确认标记不自动归入 GHIJ。", "部分进化卡的前身不在已确认对战池内；其规则经过定向场景验证，不能宣称具备自然进化卡组覆盖。", "A1 是稳定性抽样；每项效果另有定向执行/暂停恢复证据，浏览器验收覆盖共用交互控件，不代表穷举所有卡牌组合。"]}


def main():
    directory = ROOT / "artifacts/cardpool"
    paths = [directory / "ghij-backend.xml", directory / "ghij-browser.json", directory / "ghij-combinations.json", ROOT / "data/cardpool/source-exceptions.json"]
    catalog, effects = read(ROOT / "data/catalog/catalog.json"), read(ROOT / "data/simulation/effects.json")
    cases = list(ET.parse(paths[0]).getroot().iter("testcase"))
    inventory = build(catalog, effects)
    report = assess(inventory, effects, read(paths[3]), cases, read(paths[1]), read(paths[2]))
    report["evidenceHashes"] = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (directory / "authorized-scope-acceptance.json").write_bytes(encoded(report))
    (directory / "ghij-targets.json").write_bytes(encoded(inventory))
    print(json.dumps({k: report[k] for k in ("status", "gates", "counts", "backend", "A1")}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS_WITH_SOURCE_EXCEPTIONS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
