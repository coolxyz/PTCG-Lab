"""Opt-in old import names delegating to the single new implementation.

Call once in a NEW isolated worker before importing any legacy rule modules.
Existing APP processes keep their frozen files. No auto-activation on import.
Trusted line-deck Adapter callers bypass printing admission; public services use
CoreFactory.create_match instead. Old recovery records remain incompatible.
"""

import importlib
import sys
from types import ModuleType
from .identity import source_hashes


def install_legacy_imports(factory):
    from .runtime import current

    if factory.runtime is not current():
        raise ValueError("RUNTIME_MISMATCH")
    roots = ("packages.rules", "packages.engine_adapter", "packages.battle")
    if any(n == r or n.startswith(r + ".") for n in sys.modules for r in roots):
        raise RuntimeError("LEGACY_MODULES_ALREADY_LOADED")
    # Import all delegates first; a failure never installs a half-built facade.
    aliases = {}
    for name in source_hashes("ptcg_core"):
        if (
            name.startswith("rules/")
            and name.endswith(".py")
            and not name.endswith("__init__.py")
        ):
            suffix = name.removesuffix(".py").replace("/", ".")
            aliases["packages." + suffix] = importlib.import_module(
                "ptcg_core." + suffix
            )
    aliases["packages.engine_adapter.base"] = importlib.import_module("ptcg_core.base")
    aliases["packages.battle.prompts"] = importlib.import_module("ptcg_core.prompts")
    runtime = ModuleType("packages.battle.runtime")
    from .rules.adapter import Adapter, implementation_id
    from .base import RejectedCommand
    from .projection import view
    from ptcg.core.enums import PlayerId

    runtime.Adapter = Adapter
    runtime.ENGINE_VERSION = factory.rule_release_id
    runtime.implementation_id = implementation_id
    runtime.PlayerId = PlayerId
    runtime.RejectedCommand = RejectedCommand
    runtime.view = view
    aliases[runtime.__name__] = runtime
    policy = ModuleType("packages.engine_adapter.cn_format")
    from .cn_format import validate

    policy.validate = lambda entries, as_of="2026-09-30": validate(
        entries, as_of, rules=factory.bundle.documents["format"].value
    )
    aliases[policy.__name__] = policy
    for name in ("packages", *roots):
        if name not in sys.modules:
            module = ModuleType(name)
            module.__path__ = []  # No fallback to a checkout or old effects.
            sys.modules[name] = module
        if "." in name:
            parent, child = name.rsplit(".", 1)
            setattr(sys.modules[parent], child, sys.modules[name])
    for name, module in aliases.items():
        sys.modules[name] = module
        parent, child = name.rsplit(".", 1)
        setattr(sys.modules[parent], child, module)
