"""Original engine-shaped sentinel; no upstream/card assets or repository imports."""

import sys

from ptcg_core.factory import CoreFactory  # noqa: F401
from ptcg_core.engine_loader import install
from ptcg_core.identity import source_hashes


def main():
    assert not any(
        n.split(".")[0] in {"packages", "apps", "ptcg", "sqlite3", "torch", "fastapi"}
        for n in sys.modules
    )
    assert "rules/engine.py" in source_hashes("ptcg_core")
    before = list(sys.path)
    install(
        {
            "ptcg/__init__.py": b'SENTINEL = "original"\n',
            "ptcg/nested/value.py": b"VALUE = 7\n",
        }
    )
    import ptcg.nested.value

    assert ptcg.nested.value.VALUE == 7
    assert sys.path == before
    try:
        import ptcg.unlisted
    except ModuleNotFoundError as exc:
        assert str(exc) == "UNLISTED_ENGINE_MODULE"
    else:
        raise AssertionError("unlisted import accepted")


if __name__ == "__main__":
    main()
