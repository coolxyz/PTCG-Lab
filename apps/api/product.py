"""Explicit integrated product entry; uses one local HTTP server."""

from pathlib import Path
from ptcg_desktop_api.launch import main
from apps.api.main import create_app


def product_factory():
    return create_app()


if __name__ == "__main__":
    main(
        local_factory=product_factory,
        local_build=Path(__file__).resolve().parents[1] / "web/dist",
    )
