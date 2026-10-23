"""Content identity of installed Python code, independent of checkout paths.

Release assembly may additionally pin wheel archive digests. This runtime identity
hashes installed sources instead: wheel ZIP timestamps are not execution inputs.
"""

from importlib.resources import files
import sys

from ptcg_engine_provider.values import canonical, sha256


def source_hashes(package):
    result = {}

    def visit(node, prefix):
        for child in sorted(node.iterdir(), key=lambda p: p.name):
            name = prefix + child.name
            if child.is_dir() and child.name != "__pycache__":
                visit(child, name + "/")
            elif child.is_file() and child.name.endswith(".py"):
                result[name] = sha256(child.read_bytes())

    visit(files(package), "")
    if not result:
        raise ValueError("INSTALLED_SOURCES_UNAVAILABLE")
    return result


def execution_identity(bundle):
    inputs = {
        "schema": "core-execution-v2",
        "bundleId": bundle.bundle_id,
        "core": source_hashes("ptcg_core"),
        "provider": source_hashes("ptcg_engine_provider"),
        "loguru": source_hashes("loguru"),
        "python": list(sys.version_info[:3]),
        "interpreter": sys.implementation.name,
    }
    return "rules-core-v2-" + sha256(canonical(inputs))
