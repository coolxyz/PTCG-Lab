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
from ptcg_core.events import public_effects
from ptcg_core.results import explain
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
    if request["operation"] in ("project", "command"):
        match = factory.restore_match(request["recovery"])
        before = {seat: match.project_view(seat) for seat in ("PLAYER1", "PLAYER2")}
        if request["operation"] == "command":
            match.apply_command(request["seat"], request["command"])
        views = {seat: match.project_view(seat) for seat in before}
        return {
            "recovery": match.export_recovery_record(),
            "views": views,
            "effects": {
                seat: (
                    public_effects(
                        before[seat]["observation"], views[seat]["observation"]
                    )
                    if request["operation"] == "command"
                    else []
                )
                for seat in views
            },
            "results": {seat: explain(views[seat]) for seat in views},
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
        raw = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
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
