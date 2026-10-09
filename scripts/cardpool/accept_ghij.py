"""Version-bound GHIJ acceptance; subset evidence cannot close full-pool gates."""

from collections import Counter
import hashlib
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.cardpool.reprints import read, encoded  # noqa: E402
from scripts.cardpool.scope_inventory import build  # noqa: E402
from packages.battle.runtime import ENGINE_VERSION  # noqa: E402
from packages.battle.agent import VERSION as AI_VERSION  # noqa: E402
from packages.simulation.registry import VERSION as EFFECT_VERSION  # noqa: E402
from packages.cardpool.scope import VERSION as SCOPE_VERSION  # noqa: E402


def assess(inventory, matches, backend, browser, release, ui):
    counts = inventory["counts"]
    games = matches.get("games", 0)
    rows = matches.get("results", [])
    same_versions = all(
        matches.get(k) == v
        for k, v in {
            "engineVersion": ENGINE_VERSION,
            "releaseVersion": EFFECT_VERSION,
            "scopeVersion": SCOPE_VERSION,
            "catalogVersion": inventory["catalogVersion"],
            "aiVersion": AI_VERSION,
        }.items()
    )
    backend_pass = backend.get("tests", 0) > 0 and not any(
        backend.get(k, 0) for k in ("errors", "failures", "skipped")
    )
    browser_pass = browser.get("expected", 0) > 0 and not any(
        browser.get(k, 0) for k in ("unexpected", "flaky", "skipped")
    )
    statuses = dict(Counter(r["status"] for r in rows))
    families = {}
    for row in rows:
        for deck in set(row.get("decks", [])):
            family = families.setdefault(str(deck), Counter())
            family["games"] += 1
            family[row["status"]] += 1
            family["gamesWithFallback"] += int(row.get("fallbacks", 0) > 0)
    stability = bool(
        same_versions
        and games >= read(ROOT / 'data/cardpool/mechanism-workflow.json')['releaseGames']
        and len(rows) == games
        and statuses == matches.get("statuses")
        and statuses.get("finished", 0) / games >= 0.995
        and not statuses.get("error", 0)
    )
    effects = {e["effectKey"] for e in release["effects"]}
    observed = {
        key.split(":", 1)[0]
        for key, count in matches.get("actionCounts", {}).items()
        if count
    }
    source = not counts["unresolvedMarks"] and not any(
        counts["gaps"].get(k, 0)
        for k in (
            "PRINTING_IDENTITY_CONFLICT",
            "PRINTING_REVIEW_REQUIRED",
            "RELEASE_UNKNOWN",
        )
    )
    all_effects = source and counts["targets"] > 0 and counts["unsupported"] == 0
    ui_complete = bool(
        ui.get("scopeVersion") == SCOPE_VERSION
        and ui.get("engineVersion") == ENGINE_VERSION
        and set(ui.get("effects", [])) == effects
        and ui.get("allInteractionsPassed") is True
        and browser_pass
    )
    gates = {
        "P4.0_sourceClosure": source,
        "P4.1_allGHIJEffects": all_effects and backend_pass,
        "P4.2_to_4.5_allMechanismUI": all_effects and ui_complete,
        "P4.6_fullTargetStability": all_effects
        and stability
        and effects <= observed
        and not matches.get("unavailableEvolutionChains"),
    }
    return {
        "status": "PASS" if all(gates.values()) else "INCOMPLETE",
        "fullP4Gates": gates,
        "scopedChecks": {
            "backend": {"passed": backend_pass, **backend},
            "browser": {"passed": browser_pass, **browser},
            "publishedSubsetA1": {
                "passed": stability,
                "games": games,
                "versionsMatch": same_versions,
                "statuses": statuses,
                "fallbacks": matches.get("fallbacks"),
                "observedEffectCount": len(observed),
                "unobservedEffects": sorted(effects - observed),
                "unavailableEvolutionChains": matches.get(
                    "unavailableEvolutionChains", []
                ),
                "familyStats": {k: dict(v) for k, v in families.items()},
            },
        },
        "remaining": {
            "unsupportedPrintings": counts["unsupported"],
            "unresolvedMarks": counts["unresolvedMarks"],
            "sourceGaps": counts["gaps"],
        },
    }


def main():
    directory = ROOT / "artifacts/cardpool"
    catalog, release = (
        read(ROOT / "data/catalog/catalog.json"),
        read(ROOT / "data/simulation/effects.json"),
    )
    inventory = build(catalog, release)
    backend = Counter()
    backend_path = directory / "ghij-backend.xml"
    if backend_path.exists():
        for suite in ET.parse(backend_path).getroot().iter("testsuite"):
            backend.update(
                {
                    key: int(suite.get(key, 0))
                    for key in ("tests", "failures", "errors", "skipped")
                }
            )
    def optional(path):
        return read(path) if path.exists() else {}
    matches = optional(directory / "ghij-combinations.json")
    browser = optional(directory / "ghij-browser.json").get("stats", {})
    report = assess(
        inventory,
        matches,
        backend,
        browser,
        release,
        optional(ROOT / "data/cardpool/ui-coverage.json"),
    )
    report.update(
        schema="p4-ghij-acceptance-v1",
        scopeVersion=SCOPE_VERSION,
        catalogVersion=catalog["version"],
        engineVersion=ENGINE_VERSION,
        effectRelease=EFFECT_VERSION,
        aiVersion=AI_VERSION,
        denominatorVersion=inventory["denominatorVersion"],
        counts=inventory["counts"],
        limits=[
            "A1 run covers the published subset, not all GHIJ cards.",
            "Unknown marks stay unresolved rather than being silently removed.",
            "Full UI/interaction closure requires explicit effect coverage evidence; card counts alone cannot pass it.",
        ],
        evidenceHashes={
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                backend_path,
                directory / "ghij-combinations.json",
                directory / "ghij-browser.json",
                ROOT / "data/cardpool/plain-pokemon.json",
                directory / "training-sample.jsonl",
            ]
            if p.exists()
        },
    )
    (directory / "ghij-targets.json").write_bytes(encoded(inventory))
    (directory / "ghij-acceptance.json").write_bytes(encoded(report))
    current = directory / "acceptance.json"
    historical = directory / "legacy-acceptance.json"
    if current.exists() and not historical.exists():
        historical.write_bytes(current.read_bytes())
    current.write_bytes(encoded(report))
    print(
        encoded(
            {k: v for k, v in report.items() if k not in ("evidenceHashes", "limits")}
        ).decode()
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
