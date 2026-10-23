"""Original miniature assets. Engine text must never be executed by a provider."""

from pathlib import Path
import tempfile

from ptcg_engine_provider.bundle import ABI, DirectoryEngineProvider
from ptcg_engine_provider.catalog import semantic_catalog
from ptcg_engine_provider.values import canonical, sha256


def catalog():
    card = {
        "printingId": "synthetic-basic",
        "engineId": "SYN-001",
        "engineLine": "SyntheticBasic SYN 1",
        "nameLimitKey": "SyntheticBasic",
        "sourceVerified": True,
        "region": "CN",
        "language": "zh-Hans",
        "releasedAt": "2026-01-01",
        "mark": "G",
        "category": "宝可梦",
        "isBasicPokemon": True,
        "aceSpec": False,
        "effectStatus": "verified",
        "effectEvidence": ["original-test"],
        "images": [{"url": "synthetic-image"}],
    }
    energy = {
        **card,
        "printingId": "synthetic-energy",
        "engineId": "SYN-002",
        "engineLine": "SyntheticEnergy SYN 2",
        "nameLimitKey": "SyntheticEnergy",
        "mark": None,
        "category": "能量",
        "isBasicPokemon": False,
        "basicEnergyType": "fire",
    }
    return {
        "asOf": "2026-10-01",
        "formatId": "synthetic-format",
        "cards": [card, energy],
    }


def payloads():
    return [
        ("catalog.json", "catalog", canonical(semantic_catalog(catalog()))),
        (
            "format.json",
            "format",
            canonical(
                {
                    "id": "synthetic-format",
                    "effectiveAt": "2026-01-01",
                    "allowedMarks": ["G"],
                    "basicEnergyTypes": ["fire"],
                    "legacyReprintNames": [],
                    "deckSize": 60,
                    "sameNameLimit": 4,
                    "aceSpecLimit": 1,
                }
            ),
        ),
        (
            "scope.json",
            "scope",
            canonical(
                {
                    "id": "synthetic-scope",
                    "allowedMarks": ["G"],
                    "basicEnergyTypes": ["fire"],
                }
            ),
        ),
        (
            "effects.json",
            "effects",
            canonical(
                {
                    "effects": [
                        {
                            "effectKey": "SYN-001",
                            "status": "experimental",
                            "printings": ["synthetic-basic"],
                        },
                        {
                            "effectKey": "SYN-002",
                            "status": "experimental",
                            "printings": ["synthetic-energy"],
                        },
                    ]
                }
            ),
        ),
        (
            "mapping.json",
            "mapping",
            canonical({"schema": "engine-mapping-v1", "printings": {}}),
        ),
        ("specs.json", "specs", canonical({"cards": []})),
        ("license.txt", "license", b"Original synthetic test asset; MIT."),
        (
            "ptcg/__init__.py",
            "engine",
            b'raise RuntimeError("engine must not be executed")\n',
        ),
    ]


def write_bundle(root, items=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, role, content in payloads() if items is None else items:
        relative = "assets/" + name
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        rows.append(
            {
                "name": name,
                "role": role,
                "path": relative,
                "size": len(content),
                "sha256": sha256(content),
            }
        )
    manifest = {
        "schema": 1,
        "engineAbi": ABI,
        "upstreamCommit": "a" * 40,
        "components": rows,
    }
    return write_manifest(root, manifest)


def write_manifest(root, manifest):
    raw = canonical(manifest)
    (Path(root) / "bundle.json").write_bytes(raw)
    return sha256(raw)


def smoke():
    with tempfile.TemporaryDirectory() as tmp:
        pin = write_bundle(tmp)
        bundle = DirectoryEngineProvider(Path(tmp), pin).load()
        entries = [
            {"printingId": "synthetic-basic", "quantity": 4},
            {"printingId": "synthetic-energy", "quantity": 56},
        ]
        assert bundle.effect_support(entries).supported
        assert bundle.scope_support(entries).supported
        assert bundle.catalog.resolve_engine_lines(entries) == (
            "4 SyntheticBasic SYN 1",
            "56 SyntheticEnergy SYN 2",
        )
        assert bundle.rule_release_id(
            core_sha256="b" * 64, provider_sha256="c" * 64
        ).startswith("rules-v2-")
        import sys

        assert not any(
            n.split(".")[0]
            in {"ptcg", "packages", "apps", "sqlite3", "fastapi", "torch"}
            for n in sys.modules
        )


if __name__ == "__main__":
    smoke()
