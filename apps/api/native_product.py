"""Bundled desktop entry; writable product files belong to the native profile."""

import os
import sys
from pathlib import Path
from ptcg_desktop_api.native import main
from ptcg_desktop_api.installation import startup_guard


def product_factory(state):
    # Propagate read-only installation behavior to simulation subprocesses too.
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    # Import only after the parent has acquired the private profile lock.
    from apps.api.main import create_app
    from apps.api.product_online import ProductOnline

    app = create_app(state / "app.sqlite")
    app.state.online_product = ProductOnline(
        app.state.store, app.state.card_media, app.state.sync.images
    )
    return app


if __name__ == "__main__":
    raise SystemExit(
        main(
            build=Path(__file__).resolve().parents[1] / "desktop-web/dist",
            local_factory=product_factory,
            preflight=lambda state: startup_guard(
                state, Path(__file__).resolve().parents[3]
            ),
        )
    )
