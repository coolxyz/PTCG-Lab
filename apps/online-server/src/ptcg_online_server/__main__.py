"""Run a dedicated API process. Deployment supplies TLS at its private gateway."""

import os
import sys
import uvicorn
from .api import create_app
from .config import Settings


def main():
    try:
        settings = Settings.from_env()
        port = int(os.environ.get("PTCG_ONLINE_V2_PORT", "8784"))
        host = os.environ.get("PTCG_ONLINE_V2_BIND", "127.0.0.1")
        if (
            not 1024 <= port <= 65535
            or port in {8765, 8770, 8443}
            or host not in {"127.0.0.1", "0.0.0.0"}
        ):
            raise ValueError("INVALID_BIND")
    except ValueError:
        print("Invalid online v2 deployment configuration.", file=sys.stderr)
        return 1
    uvicorn.run(
        create_app(settings),
        host=host,
        port=port,
        proxy_headers=False,
        access_log=False,
        server_header=False,
        limit_concurrency=settings.max_inflight,
        timeout_keep_alive=5,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
