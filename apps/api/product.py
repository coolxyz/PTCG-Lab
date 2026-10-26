"""Explicit integrated product entry; uses one local HTTP server."""

from pathlib import Path
from ptcg_desktop_api.launch import main
from apps.api.main import create_app
from apps.api.product_online import ProductOnline


def product_factory():
    app = create_app()
    app.state.online_product = ProductOnline(app.state.store, app.state.card_media, app.state.sync.images)
    return app


if __name__ == "__main__":
    main(
        local_factory=product_factory,
        local_build=Path(__file__).resolve().parents[1] / "web/dist",
    )
