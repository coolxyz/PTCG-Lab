"""Report P4 coverage without treating a supported-pool run as full-pool proof."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.cardpool.reprints import read, encoded  # noqa: E402
from packages.battle.agent import VERSION as AI_VERSION  # noqa: E402


def coverage(inventory, catalog, release):
    cards = {c["printingId"]: c for c in catalog["cards"]}
    released_aliases = {p for e in release["effects"] for p in e["printings"]}
    candidates = [
        c
        for c in inventory["cards"]
        if c["releaseStatus"] == "released"
        and c["formatStatus"]
        in ("current-mark", "basic-energy", "legacy-reprint-review")
    ]
    missing = []
    for candidate in candidates:
        pid = candidate["printingId"]
        card = cards.get(pid, {})
        if not (
            pid in released_aliases
            and card.get("effectStatus") == "verified"
            and card.get("sourceVerified")
        ):
            missing.append(
                {
                    "printingId": pid,
                    "cardPage": candidate["cardPage"],
                    "gaps": candidate["gaps"],
                }
            )
    source_complete = bool(inventory.get("complete"))
    return {
        "asOf": inventory["asOf"],
        "formatId": inventory["formatId"],
        "enumeratedPrintings": len(inventory["cards"]),
        "candidateLegalPrintings": len(candidates),
        "supportedCandidates": len(candidates) - len(missing),
        "unsupportedCandidates": len(missing),
        "unsupportedRulePages": len({c["cardPage"] for c in missing}),
        "applicationPrintings": len(cards),
        "applicationSupportedPrintings": sum(
            c.get("effectStatus") == "verified" for c in cards.values()
        ),
        "implementedEffects": len(release["effects"]),
        "sourceGaps": inventory["counts"]["gaps"],
        "sourceComplete": source_complete,
        "effectsComplete": source_complete and not missing and bool(candidates),
        "missing": missing,
    }


def main():
    if (ROOT / "data/cardpool/battle-scope.json").exists() and "--legacy" not in sys.argv:
        from scripts.cardpool.accept_ghij import main as ghij_main
        return ghij_main()
    directory = ROOT / "artifacts/cardpool"
    inventory = read(directory / "target-inventory.json")
    catalog = read(ROOT / "data/catalog/catalog.json")
    release_file = ROOT / "data/simulation/effects.json"
    report = coverage(inventory, catalog, read(release_file))
    release_hash = hashlib.sha256(release_file.read_bytes()).hexdigest()
    backend = directory / "backend.xml"
    test_counts = Counter()
    if backend.exists():
        for suite in ET.parse(backend).getroot().iter("testsuite"):
            test_counts.update(
                {
                    key: int(suite.get(key, 0))
                    for key in ("tests", "failures", "errors", "skipped")
                }
            )
    matches_file = directory / "a1-combinations.json"
    matches = read(matches_file) if matches_file.exists() else {}
    games = matches.get("games", 0)
    statuses = matches.get("statuses", {})
    browser_file = directory / "browser.json"
    browser = read(browser_file).get("stats", {}) if browser_file.exists() else {}
    backend_pass = test_counts["tests"] > 0 and not (
        test_counts["errors"] or test_counts["failures"] or test_counts["skipped"]
    )
    a1_pass = (
        games >= 10000
        and matches.get("aiVersion") == AI_VERSION
        and matches.get("releaseVersion") == release_hash
        and len(matches.get("results", [])) == games
        and dict(Counter(r["status"] for r in matches["results"])) == statuses
        and statuses.get("finished", 0) / games >= 0.995
        and not statuses.get("error", 0)
    )
    browser_pass = browser.get("expected", 0) > 0 and not any(
        browser.get(k, 0) for k in ("unexpected", "flaky", "skipped")
    )
    report.update(
        schema="p4-acceptance-v1",
        status="INCOMPLETE",
        catalogVersion=catalog["version"],
        effectRelease=release_hash,
        scopedChecks={
            "backend": {"passed": backend_pass, **test_counts},
            "browser": {"passed": browser_pass, **browser},
            "a1SupportedPool": {
                "passed": a1_pass,
                "games": games,
                "statuses": statuses,
                "fallbacks": matches.get("fallbacks"),
                "aiVersion": matches.get("aiVersion"),
            },
        },
        fullP4Gates={
            "P4.0_sourceClosure": report["sourceComplete"],
            "P4.1_allEffects": report["effectsComplete"],
            "P4.2_to_4.5_fullPoolUI": False,
            "P4.6_fullPoolStability": False,
        },
        limits=[
            "Source inventory is provisional; unresolved printings are retained rather than excluded from the goal.",
            "Supported-pool A1 matches do not certify unsupported mechanisms or AI competitive strength.",
            "Full-pool UI, directed rule coverage and mechanism-stratified stability remain outstanding.",
        ],
        evidenceHashes={
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                directory / "target-inventory.json",
                ROOT / "data/cardpool/source-index.json",
                ROOT / "data/cardpool/reprints.json",
                backend,
                matches_file,
                browser_file,
            )
            if p.exists()
        },
    )
    (directory / "acceptance.json").write_bytes(encoded(report))
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("missing", "evidenceHashes")},
            ensure_ascii=False,
            indent=2,
        )
    )
    # A nonzero status is intentional until every P4 gate has genuine evidence.
    return 0 if all(report["fullP4Gates"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
