"""One pinned rule bundle per worker process; activation is explicit and sticky.

Call before serving matches. A failed activation poisons this worker; start a new
process instead of attempting to roll back partially imported engine classes.
"""

from dataclasses import dataclass
import sys
from threading import RLock

from ptcg_engine_provider.bundle import RuleBundle
from .identity import execution_identity


@dataclass(frozen=True, slots=True)
class Runtime:
    bundle: RuleBundle
    release_id: str


_lock = RLock()
_active = None
_ready = False
_poisoned = False


def current():
    if _active is None or _poisoned:
        raise RuntimeError("CORE_NOT_ACTIVE")
    return _active


def activate(bundle):
    global _active, _ready, _poisoned
    if not isinstance(bundle, RuleBundle):
        raise TypeError("VERIFIED_RULE_BUNDLE_REQUIRED")
    with _lock:
        if _poisoned:
            raise RuntimeError("CORE_ACTIVATION_FAILED")
        if _active is not None:
            if not _ready or _active.bundle.bundle_id != bundle.bundle_id:
                raise RuntimeError("RULE_BUNDLE_CONFLICT")
            return _active
        if any(n == "ptcg" or n.startswith("ptcg.") for n in sys.modules):
            raise RuntimeError("ENGINE_ALREADY_LOADED")
        _active = Runtime(bundle, execution_identity(bundle))
        try:
            from .engine_loader import install

            install(bundle.engine_sources())
            from ptcg.core.card_registry import registry

            registry.list_all()  # Fail before any match, never silently skip broken cards.
            from .rules.adapter import Adapter  # noqa: F401
            from loguru import logger

            logger.disable("ptcg")
            _ready = True
        except BaseException:
            _poisoned = True
            raise
        return _active
