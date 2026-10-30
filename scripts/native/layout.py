"""Explicit product inputs shared by offline kit and native payload builders."""

PROJECTS = (
    "packages/battle-protocol",
    "packages/engine-provider",
    "packages/online-client",
    "apps/desktop-api",
)
DATA = (
    "catalog/catalog.json",
    "catalog/card-details.json",
    "engine/catalog.json",
    "simulation/effects.json",
    "battle/h2-presets-v1.json",
    "cardpool/plain-pokemon.json",
    "cardpool/battle-scope.json",
    "cardpool/source-exceptions.json",
    "cardpool/species-names.json",
    "cardpool/mechanism-workflow.json",
)


ARTWORK_INDEX = "resources/artwork/builtin-index.json"


def install_artwork_index(root, assets, app):
    """Validate the source-owned display index against the explicit asset input."""
    import json
    from pathlib import Path
    import shutil
    from scripts.public.policy import safe_file
    from packages.sync.artwork import BuiltinArtwork

    catalog = safe_file(assets, "data/catalog/catalog.json")
    known = {row["printingId"]: row for row in json.loads(catalog.read_bytes())["cards"]}
    BuiltinArtwork(None, root=root, known=known, catalog_path=catalog)
    source = safe_file(root, ARTWORK_INDEX)
    target = Path(app) / ARTWORK_INDEX
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
