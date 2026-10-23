"""Registry of explicitly listed engine modules; no directory scan or skip-on-error."""

import importlib
import inspect

from .runtime import current
from ptcg.core.card import Card


class CardRegistry:
    def __init__(self):
        self._cards = {}
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        cards = {}
        for path in sorted(current().bundle.engine_sources()):
            if not path.startswith("ptcg/cards/") or path.endswith("/__init__.py"):
                continue
            name = path.removesuffix(".py").replace("/", ".")
            module = importlib.import_module(name)
            for _, cls in inspect.getmembers(module, inspect.isclass):
                if cls is Card or not issubclass(cls, Card) or cls.__module__ != name:
                    continue
                card_id = cls().id
                if card_id:
                    # Preserve deterministic legacy precedence for duplicate IDs.
                    cards[card_id] = cls
        self._cards = cards
        self._loaded = True

    def get(self, card_id):
        self._ensure_loaded()
        return self._cards.get(card_id)

    def get_by_set_and_number(self, set_name, number):
        return self.get(f"{set_name}-{number}")

    def list_all(self):
        self._ensure_loaded()
        return list(self._cards)

    def describe(self, set_name, number):
        cls = self.get_by_set_and_number(set_name, number)
        if cls is None:
            return None
        card = cls()
        return {
            **card.get_info(),
            "id": card.id,
            "set_name": card.set_name,
            "number": card.number,
        }


registry = CardRegistry()


class _CardListProxy:
    def __getitem__(self, key):
        if key == "NONE":
            return None
        registry._ensure_loaded()
        return registry._cards[key]

    def get(self, key, default=None):
        if key == "NONE":
            return None
        return registry.get(key) or default


CardList = _CardListProxy()
