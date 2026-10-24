"""One approved rule execution in a disposable process, with no DB credentials.

The parent enforces time and concurrency bounds. No network endpoint accepts this
private request format. Each invocation activates exactly one immutable bundle.
"""

from contextlib import redirect_stdout
import json
import os
from threading import Thread
import time
import sys
from ptcg_core.factory import CoreFactory
from ptcg_core.identity import execution_identity
from ptcg_engine_provider.bundle import DirectoryEngineProvider
from ptcg_engine_provider.values import canonical

MAX_RESULT = 8 * 1024 * 1024


def execute(request):
    bundle = DirectoryEngineProvider(request["directory"], request["pin"]).load()
    if execution_identity(bundle) != request["release"]:
        raise ValueError("UNAPPROVED_RULE")
    factory = CoreFactory(bundle)
    as_of = bundle.catalog.document.value["asOf"]
    if request["operation"] == "probe":
        return {"release": factory.rule_release_id, "asOf": as_of}
    if request["operation"] == "validate":
        entries = request["entries"]
        try:
            if not factory.validate_deck(entries, as_of=as_of)["playable"]:
                return {"rejected": True}
        except ValueError:
            return {"rejected": True}
        return {
            "entries": [
                {"printingId": e.printing_id, "quantity": e.quantity}
                for e in bundle.catalog.normalize(entries)
            ],
            "asOf": as_of,
        }
    if request["operation"] != "initialize" or request["as_of"] != as_of:
        raise ValueError("INVALID_WORKER_OPERATION")
    match = factory.create_match(
        request["deck1"], request["deck2"], seed=request["seed"], as_of=as_of
    )
    return {"recovery": match.export_recovery_record()}


def watch_parent(parent):
    # On the supported POSIX hosts, a forcibly killed API must not leave an
    # unbounded rule process behind after its subprocess timeout owner dies.
    while os.getppid() == parent:
        time.sleep(0.5)
    os._exit(1)


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("REQUEST_TOO_LARGE")
        request = json.loads(raw)
        if os.name == "posix":
            Thread(target=watch_parent, args=(request["parent"],), daemon=True).start()
        # Rule logging/prints must never corrupt the private JSON transport.
        with redirect_stdout(sys.stderr):
            result = execute(request)
        encoded = canonical(result)
        if len(encoded) > MAX_RESULT:
            raise ValueError("RESULT_TOO_LARGE")
        sys.stdout.buffer.write(encoded)
        return 0
    except Exception:
        # Do not emit traceback, card data, source paths or hidden state.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
