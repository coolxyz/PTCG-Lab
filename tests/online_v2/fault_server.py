"""Test-only fault factory. No fault switch exists in the deployed API."""

import asyncio
import os
from pathlib import Path
from ptcg_online_server.api import create_app as production_app
from ptcg_online_server import worker


def create_app():
    directory = Path(os.environ["PTCG_TEST_FAULT_DIRECTORY"])
    if os.environ["PTCG_TEST_FAULT_MODE"] == "compute":
        original = worker.subprocess.run

        def run(args, **kwargs):
            if args[-1] == "ptcg_online_server.rule_worker":
                args = [*args[:-1], "tests.online_v2.fault_worker"]
                kwargs["env"] = {
                    **kwargs["env"],
                    "PYTHONPATH": os.pathsep.join(
                        (
                            kwargs["env"]["PYTHONPATH"],
                            str(Path(__file__).resolve().parents[2]),
                        )
                    ),
                    "PTCG_TEST_FAULT_DIRECTORY": str(directory),
                }
            return original(args, **kwargs)

        worker.subprocess.run = run
    app = production_app()

    async def barrier(scope, receive, send):
        async def paused(message):
            if (
                os.environ["PTCG_TEST_FAULT_MODE"] == "response"
                and scope.get("path", "").endswith("/commands")
                and message["type"] == "http.response.start"
                and message["status"] == 200
            ):
                (directory / "committed").touch()
                await asyncio.sleep(12)
            await send(message)

        await app(scope, receive, paused)

    return barrier
