"""Publish the tested closed-grammar batch, retaining historical release files."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.cardpool.reprints import read, encoded, digest  # noqa: E402
from packages.battle.runtime import ENGINE_VERSION  # noqa: E402
from packages.cardpool.scope import VERSION as SCOPE_VERSION  # noqa: E402
from packages.rules.plain import SPECS, TRAINER_SPECS, SPECIAL_ENERGY_SPECS, ENERGIES  # noqa: E402
from ptcg.core.card_registry import registry  # noqa: E402


def propose(catalog, release):
    cards = {c["printingId"]: c for c in catalog["cards"]}
    effects = {r["effectKey"]: r for r in release["effects"]}
    reviews = []
    mapping = {s["effectKey"]: s["printings"] for s in SPECS + TRAINER_SPECS + SPECIAL_ENERGY_SPECS}
    short = {"001": "GRA", "003": "WAT", "004": "LIG", "006": "FIG", "007": "DAR"}
    for number in ENERGIES:
        mapping["P4E-" + number] = ["CN:basic:" + short[number]]
    by_key = {s["effectKey"]: s for s in SPECS + TRAINER_SPECS + SPECIAL_ENERGY_SPECS}
    for key, printings in sorted(mapping.items()):
        cls = registry.get(key)
        assert cls is not None
        instance = cls()
        for pid in printings:
            c = cards[pid]
            assert c.get("catalogStatus") not in (
                "source-conflict",
                "article-missing",
                "unnumbered",
            )
            if key.startswith("P4E-"):
                assert c["category"] == "能量" and c.get("basicEnergyType")
            if key.startswith("P4P-"):
                for correction in by_key[key].get("sourceCorrections", []):
                    if correction["template"] == "header" and "hp" in correction["set"]:
                        hp = int(correction["set"]["hp"])
                        assert instance.hp == hp
                        c["hp"] = hp
                        c["metadataCorrections"] = [
                            r for r in c.get("metadataCorrections", []) if r["field"] != "hp"
                        ] + [{"field": "hp", "value": hp, "basis": "official-source-correction", **correction}]
            if key.startswith(("P4T-", "P4S-")):
                if not c.get("aceSpec") and by_key[key].get("aceSpec"):
                    # Modern GHIJ compiler selected the explicit ACE SPEC header;
                    # historical table sections must not overwrite this marker.
                    c["aceSpec"] = True
                    c["metadataCorrections"] = [{"field": "aceSpec", "value": True, "basis": "compiled-modern-ACE-SPEC-header", "revision": by_key[key]["revision"], "sourceSha256": by_key[key]["sourceSha256"]}]
                assert bool(c.get("aceSpec")) == bool(by_key[key].get("aceSpec"))
            c.update(
                sourceVerified=True,
                effectStatus="verified",
                engineId=key,
                definitionId=key,
                engineLine=f"{instance.name} {instance.set_name} {instance.number}",
                catalogStatus="mechanism-reviewed",
                reviewNote="P4 严格语法机制批次；具体机制见效果清单，逐规则与交互边界测试通过，仍属实验性支持。",
            )
            reviews.append(
                {
                    "printingId": pid,
                    "effectKey": key,
                    "cardPage": c.get("cardPage"),
                    "sources": c["sourceEvidence"],
                    "sourceCorrections": by_key.get(key, {}).get("sourceCorrections", []),
                    "printingCorrections": by_key.get(key, {}).get("printingCorrections", {}).get(pid, []),
                }
            )
        mechanisms = (
            ["basic_energy"]
            if key.startswith("P4E-")
            else ["special_energy"]
            if key.startswith("P4S-")
            else ["trainer", by_key[key]["mechanic"]["kind"]]
            if key.startswith("P4T-")
            else ["attack_damage"]
            + (["evolution"] if by_key[key]["stage"] != "BASIC" else [])
            + (
                ["pokemon_rule"]
                if by_key[key].get("pokemonRule") or by_key[key].get("pokemonType")
                else []
            )
        )
        effects[key] = {
            "effectKey": key,
            "printings": printings,
            "name": instance.name,
            "registered": True,
            "implementation": cls.__module__ + "." + cls.__name__,
            "scope": "closed-grammar-reviewed-attack-mechanisms-or-basic-energy",
            "status": "experimental",
            "rulesRevision": str(by_key[key]["revision"])
            if key in by_key
            else "basic-energy-cn-scope",
            "mechanisms": mechanisms
            + sorted(
                {
                    "ability_" + a["kind"]
                    for a in by_key.get(key, {}).get("abilities", [])
                }
            )
            + sorted(
                {
                    a["mechanic"]["kind"]
                    for a in by_key.get(key, {}).get("attacks", [])
                    if "mechanic" in a
                }
            ),
            "semantics": instance.get_info(),
            "testSuites": [
                "tests/cardpool/test_plain_rules.py",
                "tests/cardpool/test_scoped_batch.py",
                "tests/cardpool/test_attack_effects.py",
                "tests/cardpool/test_damage_shield.py",
                "tests/cardpool/test_zone_effects.py",
                "tests/cardpool/test_attack_expressions.py",
                "tests/cardpool/test_checkup.py",
                "tests/cardpool/test_field_effects.py",
                "tests/cardpool/test_composed_rules.py",
                "tests/cardpool/test_shared_abilities.py",
                "tests/cardpool/test_shared_trainers.py",
                "tests/cardpool/test_target_damage.py",
                "tests/cardpool/test_extended_mechanisms.py",
                "tests/cardpool/test_protection.py",
                "tests/cardpool/test_tool_modifiers.py",
                "tests/cardpool/test_maximum_hp.py",
                "tests/cardpool/test_passive_rules.py",
                "tests/cardpool/test_entry_effects.py",
                "tests/cardpool/test_activated_effects.py",
                "tests/cardpool/test_damage_events.py",
                "tests/cardpool/test_trainer_field_effects.py",
                "tests/cardpool/test_attack_field_effects.py",
                "tests/cardpool/test_checkup_abilities.py",
                "tests/cardpool/test_suppression.py",
                "tests/cardpool/test_special_energy.py",
                "tests/cardpool/test_stadiums.py",
                "tests/cardpool/test_board_math.py",
                "tests/cardpool/test_conditional_attacks.py",
                "tests/cardpool/test_additional_trainers.py",
                "tests/cardpool/test_additional_tools.py",
                "tests/cardpool/test_additional_abilities.py",
                "tests/cardpool/test_coin_branches.py",
                "tests/cardpool/test_activated_primitives.py",
                "tests/cardpool/test_attack_additional.py",
                "tests/cardpool/test_attack_operations.py",
                "tests/cardpool/test_timed_effects.py",
                "tests/cardpool/test_staged_attacks.py",
                "tests/cardpool/test_cosmetic_rules.py",
                "tests/cardpool/test_healing_checkup.py",
            ],
            "interactionAudit": "directed-shared-mechanisms-and-stratified-A1",
            "sourceCorrections": by_key.get(key, {}).get("sourceCorrections", []),
        }
    energy_records = read(ROOT / "data/cardpool/plain-pokemon.json").get(
        "basicEnergyPrintings", []
    )
    for row in energy_records:
        key, pid = row["effectKey"], row["printingId"]
        c, effect = cards[pid], effects[key]
        instance = registry.get(key)()
        assert c.get("basicEnergyType") and c.get("catalogStatus") in (
            "listed",
            "mechanism-reviewed",
        )
        c.update(
            sourceVerified=True,
            effectStatus="verified",
            engineId=key,
            definitionId=key,
            engineLine=f"{instance.name} {instance.set_name} {instance.number}",
            catalogStatus="mechanism-reviewed",
            reviewNote="编号产品表确认印刷身份，基本能量规则页确认属性；复用已验证基本能量实现。",
        )
        effect["printings"] = sorted(set(effect["printings"]) | {pid})
        reviews.append({**row, "sources": c["sourceEvidence"]})
    # Fail closed if compiler changes dropped a previously admitted printing.
    live = {c["engineId"] for c in catalog["cards"] if c["effectStatus"] == "verified"}
    effects = {k: v for k, v in effects.items() if k in live}
    for key in live:
        assert key in effects and registry.get(key) is not None, ("unregistered admitted effect", key)
    release.update(
        engineVersion=ENGINE_VERSION,
        battleScopeVersion=SCOPE_VERSION,
        effects=list(effects.values()),
        scope=f"GHIJ and basic energies; {len(effects)} implemented effects; incomplete full target coverage",
    )
    catalog.pop("version", None)
    catalog["version"] = digest(catalog)
    return catalog, release, reviews


def main():
    cp, rp = ROOT / "data/catalog/catalog.json", ROOT / "data/simulation/effects.json"
    old_release = rp.read_bytes()
    old_hash = hashlib.sha256(old_release).hexdigest()
    catalog, release, reviews = propose(read(cp), read(rp))
    report = {
        "schema": "p4-mechanism-release-v1",
        "catalogVersion": catalog["version"],
        "engineVersion": ENGINE_VERSION,
        "effectRelease": hashlib.sha256(encoded(release)).hexdigest(),
        "battleScopeVersion": SCOPE_VERSION,
        "plainEffects": len(SPECS),
        "plainPrintings": sum(len(s["printings"]) for s in SPECS),
        "trainerEffects": len(TRAINER_SPECS),
        "trainerPrintings": sum(len(s["printings"]) for s in TRAINER_SPECS),
        "specialEnergyEffects": len(SPECIAL_ENERGY_SPECS),
        "specialEnergyPrintings": sum(len(s["printings"]) for s in SPECIAL_ENERGY_SPECS),
        "additionalBasicEnergies": len(ENERGIES),
        "totalEffects": len(release["effects"]),
        "supportedPrintings": sum(
            c["effectStatus"] == "verified" for c in catalog["cards"]
        ),
        "reviews": reviews,
    }
    if "--publish" not in sys.argv:
        (ROOT / "artifacts/cardpool/mechanism-preview.json").write_bytes(encoded(report))
    else:
        backup = ROOT / ".catalog/cardpool/pre-ghij-mechanisms"
        backup.mkdir(parents=True, exist_ok=True)
        db = ROOT / "data/catalog/catalog.sqlite"
        for path in (cp, rp, db):
            if not (backup / path.name).exists():
                (backup / path.name).write_bytes(path.read_bytes())
        (ROOT / "data/cardpool/release-history" / (old_hash + ".json")).write_bytes(
            old_release
        )
        stage = db.with_suffix(".mechanisms.sqlite")
        with (
            closing(sqlite3.connect(db)) as src,
            closing(sqlite3.connect(stage)) as dst,
        ):
            src.backup(dst)
            for c in catalog["cards"]:
                dst.execute(
                    "UPDATE cards SET effect_status=?,metadata_json=? WHERE printing_id=?",
                    (
                        c["effectStatus"],
                        json.dumps(c, ensure_ascii=False),
                        c["printingId"],
                    ),
                )
            dst.execute(
                "UPDATE metadata SET value=? WHERE key='version'", (catalog["version"],)
            )
            dst.commit()
            assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not dst.execute("PRAGMA foreign_key_check").fetchall()
        stage.replace(db)
        cp.write_bytes(encoded(catalog))
        rp.write_bytes(encoded(release))
        (ROOT / "artifacts/cardpool/mechanism-release.json").write_bytes(encoded(report))
    print(json.dumps({k: v for k, v in report.items() if k != "reviews"}, indent=2))


if __name__ == "__main__":
    main()
