"""Load only verified ptcg sources; no sys.path or filesystem fallback.

This executes operator-trusted code, not a sandbox for client-supplied assets.
The only rewrites are static packages.rules imports and two explicit I/O adapters.
"""

import ast
import importlib.abc
import importlib.util
import sys

_OVERRIDES = {
    "ptcg.core.card_registry": "from ptcg_core.engine_registry import CardRegistry, CardList, registry\n",
    "ptcg.utils.load_deck": "from ptcg_core.engine_deck import load_deck\n",
}


class RuleImports(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        if node.module == "packages.rules":
            node.module = "ptcg_core.rules"
        elif node.module and node.module.startswith("packages.rules."):
            node.module = "ptcg_core.rules." + node.module.removeprefix(
                "packages.rules."
            )
        return node


class VerifiedLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, sources):
        self.sources = {}
        for path, source in sources.items():
            package = path.endswith("/__init__.py")
            name = (
                path.removesuffix("/__init__.py")
                if package
                else path.removesuffix(".py")
            )
            name = name.replace("/", ".")
            if (
                not all(p.isidentifier() for p in name.split("."))
                or name in self.sources
            ):
                raise ValueError("INVALID_ENGINE_MODULE")
            self.sources[name] = (source, package)
        # Some upstream directories are namespace packages without __init__.py.
        # Derive them solely from the verified list; never consult filesystem paths.
        for name in list(self.sources):
            parts = name.split(".")
            for i in range(1, len(parts)):
                self.sources.setdefault(".".join(parts[:i]), (b"", True))

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "ptcg" and not fullname.startswith("ptcg."):
            return None
        if fullname not in self.sources:
            raise ModuleNotFoundError("UNLISTED_ENGINE_MODULE", name=fullname)
        return importlib.util.spec_from_loader(
            fullname, self, is_package=self.sources[fullname][1]
        )

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source = _OVERRIDES.get(module.__name__, self.sources[module.__name__][0])
        filename = "<verified-engine>/" + module.__name__.replace(".", "/") + ".py"
        tree = RuleImports().visit(ast.parse(source, filename=filename))
        ast.fix_missing_locations(tree)
        module.__file__ = filename
        # Audited exception to the dynamic-execution gate: only pinned engine bytes.
        exec(compile(tree, filename, "exec"), module.__dict__)


def install(sources):
    loader = VerifiedLoader(sources)
    sys.meta_path.insert(0, loader)
