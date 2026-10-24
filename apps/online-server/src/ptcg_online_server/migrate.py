"""Explicit migration command. Never run automatically when an API worker starts."""

import sys
from .config import Settings
from .errors import ServiceError
from .repository import Repository


def main():
    try:
        Repository(Settings.from_env()).migrate()
    except (ServiceError, ValueError):
        print(
            "Migration refused or unavailable; verify dedicated database and pinned schema.",
            file=sys.stderr,
        )
        return 1
    print("Online v2 schema verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
