"""Test-only barrier after real computation, before publishing its candidate.

Never packaged into the service image. Uses the production worker entrypoint,
including its parent watchdog, input/output protocol and rule activation.
"""

import os
import json
from pathlib import Path
import time
from ptcg_online_server import rule_worker


def main():
    execute = rule_worker.execute

    def paused(request):
        result = execute(request)
        if request["operation"] == "command":
            directory = Path(os.environ["PTCG_TEST_FAULT_DIRECTORY"])
            (directory / "worker-pid").write_text(
                json.dumps({"pid": os.getpid(), "parent": os.getppid()})
            )
            # Parent test kills exactly this process or its API owner. A finite
            # wait also bounds a failed test; this is not a production hook.
            until = time.monotonic() + 12
            while time.monotonic() < until:
                time.sleep(0.05)
        return result

    rule_worker.execute = paused
    return rule_worker.main()


if __name__ == "__main__":
    raise SystemExit(main())
