"""Stage explicit, trusted local rule assets; never import an engine or user state.

The result contains restricted inputs and belongs under var/ or private storage.
This is a P2-B input bundle, NOT a runnable core release or public distribution.
"""

import argparse
from pathlib import Path
import shutil
import tempfile

from ptcg_engine_provider.bundle import ABI, DirectoryEngineProvider, read_asset
from ptcg_engine_provider.catalog import semantic_catalog, semantic_mapping
from ptcg_engine_provider.values import (
    ProviderError,
    canonical,
    is_digest,
    parse_json,
    require,
    sha256,
)

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_COMMIT = "92c3cc4fe85a26f102d7bb6b3e8be7678512d2e5"
INPUTS = {
    "catalog": "data/catalog/catalog.json",
    "format": "rulesets/cn-standard-2026-09-16.json",
    "scope": "data/cardpool/battle-scope.json",
    "effects": "data/simulation/effects.json",
    "mapping": "data/engine/catalog.json",
    "specs": "data/cardpool/plain-pokemon.json",
}


def prepare(source, output, *, expected_overlay_sha256):
    source, output = Path(source), Path(output).resolve()
    require(not output.exists(), "OUTPUT_EXISTS")
    for base in (ROOT, source.resolve()):
        if output.is_relative_to(base):
            require(
                output != base and output.relative_to(base).parts[0] == "var",
                "PRIVATE_OUTPUT_REQUIRED",
            )
    require(is_digest(expected_overlay_sha256), "INVALID_MANIFEST_PIN")
    overlay_raw = read_asset(source, "artifacts/engine/overlay-hashes.json", 1_048_576)
    require(sha256(overlay_raw) == expected_overlay_sha256, "MANIFEST_MISMATCH")
    overlay = parse_json(overlay_raw)
    require(
        isinstance(overlay, dict) and 0 < len(overlay) <= 4096,
        "INVALID_ENGINE_MANIFEST",
    )
    payloads = []
    for role, name in INPUTS.items():
        content = read_asset(source, name)
        if role == "catalog":
            content = canonical(semantic_catalog(parse_json(content)))
        elif role == "mapping":
            content = canonical(semantic_mapping(parse_json(content)))
        payloads.append((role + ".json", role, content))
    payloads.append(
        (
            "engine-license.txt",
            "license",
            read_asset(source, "runtime/engine/UPSTREAM-LICENSE"),
        )
    )
    for name, expected in sorted(overlay.items()):
        require(
            isinstance(name, str)
            and name.startswith("ptcg/")
            and name.endswith(".py")
            and is_digest(expected),
            "INVALID_ENGINE_MANIFEST",
        )
        content = read_asset(source, "runtime/engine/" + name)
        require(sha256(content) == expected, "ENGINE_OVERLAY_MISMATCH")
        payloads.append((name, "engine", content))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="ptcg-rule-inputs-", dir=output.parent
    ) as temporary:
        stage = Path(temporary)
        rows = []
        for name, role, content in payloads:
            relative = "assets/" + name
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            rows.append(
                {
                    "name": name,
                    "role": role,
                    "path": relative,
                    "sha256": sha256(content),
                    "size": len(content),
                }
            )
        raw = canonical(
            {
                "schema": 1,
                "engineAbi": ABI,
                "upstreamCommit": UPSTREAM_COMMIT,
                "components": rows,
            }
        )
        (stage / "bundle.json").write_bytes(raw)
        verified = DirectoryEngineProvider(stage, sha256(raw)).load()
        report = {
            "schema": 1,
            "bundleId": verified.bundle_id,
            "manifestSha256": sha256(raw),
            "catalogVersion": verified.catalog.catalog_version,
            "engineFiles": len(overlay),
            "componentCount": len(rows),
            "sourceOverlaySha256": expected_overlay_sha256,
            "containsRestrictedAssets": True,
            "userDatabasesCopied": False,
            "engineExecuted": False,
            "productionReady": False,
        }
        # Reserve a new directory, never merge or overwrite an existing bundle.
        output.mkdir(exist_ok=False)
        try:
            for item in stage.iterdir():
                shutil.move(str(item), output / item.name)
            (output / "PREPARATION.json").write_bytes(canonical(report))
        except Exception:
            # Only this function's newly reserved output is removed on failure.
            shutil.rmtree(output)
            raise
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-overlay-sha256", required=True)
    args = parser.parse_args()
    try:
        print(
            canonical(
                prepare(
                    args.source,
                    args.output,
                    expected_overlay_sha256=args.expected_overlay_sha256,
                )
            ).decode()
        )
    except ProviderError as error:
        raise SystemExit(error.code) from None
