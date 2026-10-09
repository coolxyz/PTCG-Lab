"""Fail-closed simplified-Chinese policy prototype; requires curated printings."""

from collections import Counter
from datetime import date
from pathlib import Path
import json

RULES = json.loads(
    (
        Path(__file__).resolve().parents[2] / "rulesets/cn-standard-2026-09-16.json"
    ).read_text()
)


def validate(entries, as_of="2026-09-30"):
    issues, names = [], Counter()
    total = basics = ace = unknown_basics = 0
    if date.fromisoformat(as_of) < date.fromisoformat(RULES["effectiveAt"]):
        issues.append({"code": "SNAPSHOT_NOT_EFFECTIVE", "level": "invalid"})
    for entry in entries:
        quantity, card = entry["quantity"], entry["printing"]
        if type(quantity) is not int or quantity <= 0:
            issues.append({"code": "INVALID_QUANTITY", "level": "invalid"})
            continue
        total += quantity
        if not card.get("sourceVerified") or not card.get("printingId"):
            issues.append({"code": "PRINTING_UNVERIFIED", "level": "unknown"})
        if card.get("region") != "CN" or card.get("language") != "zh-Hans":
            issues.append(
                {"code": "NOT_SIMPLIFIED_CHINESE_PRINTING", "level": "invalid"}
            )
        released = card.get("releasedAt")
        if not released:
            issues.append({"code": "RELEASE_UNKNOWN", "level": "unknown"})
        elif date.fromisoformat(released) > date.fromisoformat(as_of):
            issues.append({"code": "NOT_RELEASED", "level": "invalid"})
        basic_energy = card.get("basicEnergyType") in RULES["basicEnergyTypes"]
        allowed = basic_energy or card.get("mark") in RULES["allowedMarks"]
        reprint = (
            card.get("nameLimitKey") in RULES["legacyReprintNames"]
            and card.get("legacyReprintVerified") is True
            and bool(card.get("latestEffectRevision"))
        )
        if not allowed and not reprint:
            issues.append(
                {
                    "code": "MARK_OR_REPRINT_NOT_ALLOWED",
                    "level": "invalid" if card.get("mark") else "unknown",
                }
            )
        if not basic_energy:
            if not card.get("nameLimitKey"):
                issues.append({"code": "NAME_GROUP_UNKNOWN", "level": "unknown"})
            else:
                names[card["nameLimitKey"]] += quantity
        if card.get("category") == "宝可梦" and card.get("isBasicPokemon") is None:
            unknown_basics += quantity
        if card.get("isBasicPokemon"):
            basics += quantity
        if card.get("aceSpec"):
            ace += quantity
        if card.get("effectStatus") != "verified":
            issues.append({"code": "EFFECT_NOT_VERIFIED", "level": "engine"})
    if total != RULES["deckSize"]:
        issues.append({"code": "DECK_SIZE", "level": "invalid"})
    if not basics:
        issues.append(
            {
                "code": "BASIC_STATUS_UNKNOWN"
                if unknown_basics
                else "NO_BASIC_POKEMON",
                "level": "unknown" if unknown_basics else "invalid",
            }
        )
    if any(n > RULES["sameNameLimit"] for n in names.values()):
        issues.append({"code": "SAME_NAME_LIMIT", "level": "invalid"})
    if ace > RULES["aceSpecLimit"]:
        issues.append({"code": "ACE_SPEC_LIMIT", "level": "invalid"})
    legal = (
        "invalid"
        if any(i["level"] == "invalid" for i in issues)
        else "unknown"
        if any(i["level"] == "unknown" for i in issues)
        else "valid"
    )
    return {
        "formatId": RULES["id"],
        "legality": legal,
        "playable": legal == "valid"
        and not any(i["level"] == "engine" for i in issues),
        "issues": issues,
    }
