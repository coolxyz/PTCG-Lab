"""Offline enrichment of the reviewed P0.1 catalog. Never promotes unknown cards."""

import json, hashlib
from pathlib import Path
from ptcg.core.card_registry import registry
from ptcg.core.enums import SuperType

ROOT = Path(__file__).resolve().parents[2]
source = ROOT / "data/engine/catalog.json"
raw = json.loads(source.read_text())
cards = []
for printing in raw["printings"].values():
    c = registry.get(printing["engineId"])()
    row = dict(printing)
    row.update(
        definitionId=printing["engineId"],
        englishName=c.name,
        category={"POKEMON": "宝可梦", "TRAINER": "训练家", "ENERGY": "能量"}[
            c.superType.name
        ],
        subtype=getattr(getattr(c, "trainerType", None), "name", None),
        pokemonType=getattr(getattr(c, "cardType", None), "name", None),
        hp=getattr(c, "hp", None),
        stage=getattr(getattr(c, "stage", None), "name", None),
        evolvesFrom=getattr(c, "evolveFrom", [])[:1],
        attacks=[
            {"name": a.name, "cost": [t.name for t in a.cost], "damage": a.damage}
            for a in getattr(c, "attacks", [])
        ],
    )
    if c.id == "TWM-200":
        row["evolvesFrom"] = ["Drakloak"]
    row["aliases"] = [c.name, printing["cnName"].replace(" ", ""), printing["engineId"]]
    cards.append(row)
out = {
    "schema": "p1-catalog-v1",
    "sourceVersion": hashlib.sha256(source.read_bytes()).hexdigest(),
    "reviewedAt": raw["reviewedAt"],
    "formatId": "cn-standard-2026-09-16",
    "cards": cards,
    "templates": [
        {
            "id": k,
            "name": {
                "gholdengo": "赛富豪ex · 大师战略",
                "dragapult": "多龙巴鲁托ex · 大师战略",
            }[k],
            "entries": v,
        }
        for k, v in raw["decks"].items()
    ],
}
out["version"] = hashlib.sha256(
    json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
(ROOT / "data/collection/catalog.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2)
)
print(f"Enriched {len(cards)} reviewed identities")
