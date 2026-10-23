"""Rule-facing catalog lookup; no collection, media, engine import or policy I/O."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from .values import JsonSnapshot, canonical, require

# Display names, images, collection counts and source pages are not rule inputs.
CARD_FIELDS = frozenset(
    {
        "printingId",
        "engineId",
        "engineLine",
        "nameLimitKey",
        "sourceVerified",
        "region",
        "language",
        "releasedAt",
        "basicEnergyType",
        "mark",
        "legacyReprintVerified",
        "latestEffectRevision",
        "category",
        "isBasicPokemon",
        "aceSpec",
        "effectStatus",
        "effectEvidence",
    }
)


@dataclass(frozen=True, slots=True)
class DeckEntry:
    printing_id: str
    quantity: int


@dataclass(frozen=True, slots=True)
class ResolvedEntry:
    quantity: int
    printing: Mapping


class CatalogProvider(Protocol):
    @property
    def catalog_version(self) -> str: ...
    def card(self, printing_id: str) -> Mapping: ...
    def normalize(self, entries: list[dict]) -> tuple[DeckEntry, ...]: ...
    def resolve(self, entries: list[dict]) -> tuple[ResolvedEntry, ...]: ...


def semantic_catalog(raw):
    """Build-time projection from a trusted catalog, never from a client deck."""
    require(
        isinstance(raw, dict) and isinstance(raw.get("cards"), list), "INVALID_CATALOG"
    )
    cards = []
    for source in raw["cards"]:
        require(isinstance(source, dict), "INVALID_CATALOG")
        card = {k: source.get(k) for k in sorted(CARD_FIELDS)}
        # Existing admission tests only inspect evidence presence, not its text.
        card["effectEvidence"] = bool(source.get("effectEvidence"))
        cards.append(card)
    require(all(isinstance(c["printingId"], str) for c in cards), "INVALID_CATALOG")
    return {
        "schema": "semantic-catalog-v1",
        "asOf": raw.get("asOf"),
        "formatId": raw.get("formatId"),
        "cards": sorted(cards, key=lambda c: c["printingId"]),
    }


def semantic_mapping(raw):
    """Strip legacy display metadata and preset decks from the engine mapping."""
    require(
        isinstance(raw, dict) and isinstance(raw.get("printings"), dict),
        "INVALID_MAPPING",
    )
    for pid, card in raw["printings"].items():
        require(
            isinstance(card, dict) and card.get("printingId") == pid, "INVALID_MAPPING"
        )
    projected = semantic_catalog({"cards": list(raw["printings"].values())})
    return {
        "schema": "engine-mapping-v1",
        "printings": {c["printingId"]: c for c in projected["cards"]},
    }


@dataclass(frozen=True, slots=True, init=False)
class MemoryCatalog:
    document: JsonSnapshot
    _cards: Mapping

    def __init__(self, document: JsonSnapshot):
        raw = document.value
        require(set(raw) == {"schema", "asOf", "formatId", "cards"}, "INVALID_CATALOG")
        require(raw["schema"] == "semantic-catalog-v1", "INVALID_CATALOG")
        require(isinstance(raw["cards"], tuple), "INVALID_CATALOG")
        cards = {}
        for card in raw["cards"]:
            require(
                isinstance(card, Mapping) and set(card) == CARD_FIELDS,
                "INVALID_CATALOG",
            )
            pid = card["printingId"]
            require(isinstance(pid, str) and 0 < len(pid) <= 256, "INVALID_CATALOG")
            require(pid not in cards, "DUPLICATE_PRINTING")
            for field in CARD_FIELDS - {
                "sourceVerified",
                "isBasicPokemon",
                "aceSpec",
                "effectEvidence",
                "legacyReprintVerified",
            }:
                require(
                    card[field] is None or isinstance(card[field], str),
                    "INVALID_CATALOG",
                )
            for field in {
                "sourceVerified",
                "isBasicPokemon",
                "aceSpec",
                "effectEvidence",
                "legacyReprintVerified",
            }:
                require(
                    card[field] is None or type(card[field]) is bool, "INVALID_CATALOG"
                )
            cards[pid] = card
        object.__setattr__(self, "document", document)
        object.__setattr__(self, "_cards", MappingProxyType(cards))

    @classmethod
    def from_catalog(cls, raw):
        return cls(JsonSnapshot.from_bytes(canonical(semantic_catalog(raw))))

    @property
    def catalog_version(self):
        return self.document.digest

    def card(self, printing_id):
        require(
            isinstance(printing_id, str) and printing_id in self._cards,
            "UNKNOWN_PRINTING",
        )
        return self._cards[printing_id]

    def normalize(self, entries):
        require(isinstance(entries, list) and len(entries) <= 500, "BAD_ENTRIES")
        counts = Counter()
        for entry in entries:
            require(
                isinstance(entry, dict) and set(entry) == {"printingId", "quantity"},
                "BAD_ENTRY",
            )
            self.card(entry["printingId"])
            q = entry["quantity"]
            require(type(q) is int and 1 <= q <= 60, "BAD_QUANTITY")
            counts[entry["printingId"]] += q
        require(all(q <= 60 for q in counts.values()), "BAD_QUANTITY")
        require(sum(counts.values()) <= 600, "TOO_MANY_CARDS")
        return tuple(DeckEntry(pid, count) for pid, count in sorted(counts.items()))

    def resolve(self, entries):
        return tuple(
            ResolvedEntry(e.quantity, self.card(e.printing_id))
            for e in self.normalize(entries)
        )

    def resolve_engine_lines(self, entries):
        """Resolve reviewed mapping only; NOT format legality or match admission."""
        lines = []
        for row in self.resolve(entries):
            card = row.printing
            require(
                card["effectStatus"] == "verified" and card["effectEvidence"] is True,
                "ENGINE_MAPPING_UNREVIEWED",
            )
            line = card["engineLine"]
            require(
                isinstance(line, str)
                and bool(line.strip())
                and not any(c in line for c in "\r\n\x00"),
                "ENGINE_MAPPING_UNREVIEWED",
            )
            lines.append(f"{row.quantity} {line}")
        return tuple(lines)
