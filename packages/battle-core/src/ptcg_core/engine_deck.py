"""Explicit in-memory engine lines only; never resolve filenames or presets."""

from ptcg.core.card_registry import registry
from ptcg.core.deck import Deck


def load_deck(lines):
    if not isinstance(lines, list) or not 1 <= len(lines) <= 60:
        raise ValueError("EXPLICIT_DECK_LINES_REQUIRED")
    cards = []
    for line in lines:
        if not isinstance(line, str) or len(line) > 1024:
            raise ValueError("INVALID_DECK_LINE")
        parts = line.split()
        if len(parts) < 4 or not parts[0].isascii() or not parts[0].isdigit():
            raise ValueError("INVALID_DECK_LINE")
        count = int(parts[0])
        if not 1 <= count <= 60 or len(cards) + count > 60:
            raise ValueError("INVALID_DECK_QUANTITY")
        cls = registry.get_by_set_and_number(parts[-2], parts[-1].zfill(3))
        if cls is None:
            raise ValueError("UNKNOWN_ENGINE_CARD")
        cards.extend(cls() for _ in range(count))
    return Deck(cards)
