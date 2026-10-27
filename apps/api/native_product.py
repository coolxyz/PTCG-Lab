"""Bundled desktop entry; writable product files belong to the native profile."""

from pathlib import Path
from ptcg_desktop_api.native import main


def product_factory(state):
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
        )
    )
