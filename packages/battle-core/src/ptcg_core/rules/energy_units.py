"""One restricted wildcard is one unit, even when it has two possible types."""
from ptcg.core.enums import CardType


def matches(unit, element):
    name = element if isinstance(element, str) else element.name
    return unit.name in (name, "ANY") or unit.name == "PSYCHIC_DARK" and name in ("PSYCHIC", "DARK")
