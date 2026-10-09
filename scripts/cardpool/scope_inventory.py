"""Freeze the GHIJ denominator independently of implemented effects."""

from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.cardpool.scope import SCOPE, VERSION, includes  # noqa: E402
from scripts.cardpool.reprints import read, encoded, digest  # noqa: E402


def build(catalog, release):
    ledger = read(ROOT / "data/cardpool/source-exceptions.json")
    exception_ids = set(ledger["printingIds"])
    aliases = {
        pid: row["effectKey"] for row in release["effects"] for pid in row["printings"]
    }
    targets, excluded, unresolved = [], [], []
    for c in catalog["cards"]:
        record = {
            "printingId": c["printingId"],
            "cardPage": c.get("cardPage"),
            "mark": c.get("mark"),
            "sources": c.get("sourceEvidence", []),
        }
        if not includes(c):
            record["reason"] = (
                "mark-outside-ghij" if c.get("mark") else "mark-unresolved"
            )
            if c.get("basicEnergyType") == "fairy":
                record["reason"] = "fairy-basic-energy-outside-scope"
            (unresolved if record["reason"] == "mark-unresolved" else excluded).append(
                record
            )
            continue
        gaps = []
        if c.get("catalogStatus") == "source-conflict":
            gaps.append("PRINTING_IDENTITY_CONFLICT")
        if not c.get("sourceVerified"):
            gaps.append("PRINTING_REVIEW_REQUIRED")
        if not c.get("releasedAt"):
            gaps.append("RELEASE_UNKNOWN")
        elif c["releasedAt"] > SCOPE["asOf"]:
            record["reason"] = "future-release"
            excluded.append(record)
            continue
        supported = bool(
            c.get("effectStatus") == "verified"
            and c.get("sourceVerified")
            and aliases.get(c["printingId"]) == c.get("engineId")
        )
        if not supported:
            gaps.append("EFFECT_UNSUPPORTED")
        targets.append(
            {
                **record,
                "basicEnergyType": c.get("basicEnergyType"),
                "effectKey": aliases.get(c["printingId"]),
                "supported": supported,
                "gaps": gaps,
            }
        )
    # The identity denominator excludes implementation flags and release hashes.
    denominator = [
        {k: v for k, v in c.items() if k not in ("effectKey", "supported", "gaps")}
        for c in targets
    ]
    exceptions = [c for c in targets if c["printingId"] in exception_ids]
    return {
        "schema": "p4-ghij-targets-v1",
        "scope": SCOPE,
        "scopeVersion": VERSION,
        "catalogVersion": catalog["version"],
        "denominatorVersion": digest(denominator),
        "sourceExceptions": {
            "status": ledger["status"],
            "authorization": ledger["authorization"],
            "printingIds": sorted(c["printingId"] for c in exceptions),
            "countsAsSupported": False,
        },
        "counts": {
            "targets": len(targets),
            "supported": sum(c["supported"] for c in targets),
            "unsupported": sum(not c["supported"] for c in targets),
            "pendingSourceExceptions": len(exceptions),
            "unsupportedOutsideExceptions": sum(
                not c["supported"] and c["printingId"] not in exception_ids
                for c in targets
            ),
            "rulePages": len({c["cardPage"] for c in targets if c["cardPage"]}),
            "unresolvedMarks": len(unresolved),
            "excluded": len(excluded),
            "marks": dict(Counter(c["mark"] or "basic-energy" for c in targets)),
            "gaps": dict(Counter(g for c in targets for g in c["gaps"])),
        },
        "targets": targets,
        "excluded": excluded,
        "unresolved": unresolved,
    }


if __name__ == "__main__":
    report = build(
        read(ROOT / "data/catalog/catalog.json"), read(ROOT / "data/simulation/effects.json")
    )
    (ROOT / "artifacts/cardpool/ghij-targets.json").write_bytes(encoded(report))
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
