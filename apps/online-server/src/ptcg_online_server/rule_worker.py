"""Serial approved rule execution in an isolated process, with no DB credentials.

The parent enforces time and concurrency bounds. No network endpoint accepts this
private request format. Each process activates exactly one immutable bundle.
"""

from contextlib import redirect_stdout
import json
import os
import select
from threading import Thread
import time
import sys
from ptcg_core.events import public_effects
from ptcg_core.results import explain
from ptcg_engine_provider.values import canonical
from .metrics import Metrics
from .worker_runtime import RuleRuntime, budget
from .public_events import action_events, coin_results, selection_source_ref

MAX_RESULT = 8 * 1024 * 1024


def execute(request, metrics=None, runtime=None):
    metrics = metrics if metrics is not None else Metrics()
    runtime = runtime or RuleRuntime(request, metrics)
    bundle, factory = runtime.bundle, runtime.factory
    if request["operation"] == "inspect":
        return {"cached": runtime.cached(request, metrics)}
    if request["operation"] == "confirm":
        return runtime.confirm(request, metrics)
    if request["operation"] == "discard":
        return runtime.discard(request)
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
        match = runtime.restore(request, metrics)
        before = {seat: match.project_view(seat) for seat in ("PLAYER1", "PLAYER2")}
        selection_source = None
        if request["operation"] == "command":
            selection_source = selection_source_ref(
                match._adapter, before[request["seat"]]
            )
            with (
                metrics.time("rule.compute"),
                budget(runtime.limits.get("compute", 20)),
            ):
                match.apply_command(request["seat"], request["command"])
        # Internal adapter diagnostics are reduced to exact public coin outcomes;
        # no arbitrary logs, private choices or recovery data enter these events.
        events = (
            action_events(
                before[request["seat"]],
                request["seat"],
                request["command"]["choice"],
                coin_results(match._adapter.info),
                selection_source=selection_source,
            )
            if request["operation"] == "command"
            else []
        )
        views = {seat: match.project_view(seat) for seat in before}
        record = match.export_recovery_record()
        if request["operation"] == "project":
            runtime.remember(request.get("match_id"), record, match, metrics)
        ticket = (
            runtime.candidate(request.get("match_id"), record, match)
            if request["operation"] == "command"
            else None
        )
        return {
            "_cacheTicket": ticket,
            "recovery": record,
            "views": views,
            "events": {seat: events for seat in views},
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
    with metrics.time("rule.compute"), budget(runtime.limits.get("compute", 20)):
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
    persistent = "--persistent" in sys.argv
    runtime, identity, generation, last_request = None, None, None, 0
    try:
        while True:
            if (
                persistent
                and runtime is not None
                and not select.select(
                    [sys.stdin.buffer], [], [], min(5, runtime.limits.get("idle", 60))
                )[0]
            ):
                if runtime.prune(Metrics()):
                    return 0
                continue
            raw = (
                sys.stdin.buffer.readline(16 * 1024 * 1024 + 1)
                if persistent
                else sys.stdin.buffer.read(16 * 1024 * 1024 + 1)
            )
            if not raw:
                return 0
            if len(raw) > 16 * 1024 * 1024 or (persistent and not raw.endswith(b"\n")):
                raise ValueError("REQUEST_TOO_LARGE")
            request = json.loads(raw)
            if identity is None:
                identity = tuple(
                    request[k] for k in ("parent", "directory", "pin", "release")
                )
                generation = request.get("generation")
                if os.name == "posix":
                    Thread(
                        target=watch_parent, args=(request["parent"],), daemon=True
                    ).start()
            if identity != tuple(
                request[k] for k in ("parent", "directory", "pin", "release")
            ):
                raise ValueError("WORKER_IDENTITY_CHANGED")
            if persistent:
                if (
                    request["generation"] != generation
                    or request["requestId"] != last_request + 1
                ):
                    raise ValueError("STALE_WORKER_REQUEST")
                last_request = request["requestId"]
            with redirect_stdout(sys.stderr):
                metrics = Metrics()
                if persistent and runtime is None:
                    runtime = RuleRuntime(request, metrics)
                result = (
                    execute(request, metrics, runtime)
                    if persistent
                    else execute(request, metrics)
                )
                if runtime is not None:
                    result["_recycle"] = runtime.prune(metrics)
                    result["_rssBytes"] = runtime.rss()
                result["_timings"] = {
                    name: row["seconds"]
                    for name, row in metrics.snapshot()["metrics"].items()
                }
            encoded = canonical(
                {"generation": generation, "requestId": last_request, "result": result}
                if persistent
                else result
            )
            if len(encoded) > MAX_RESULT:
                raise ValueError("RESULT_TOO_LARGE")
            sys.stdout.buffer.write(encoded + (b"\n" if persistent else b""))
            sys.stdout.buffer.flush()
            if not persistent or result.get("_recycle"):
                return 0
    except Exception:
        return 1  # Never emit private diagnostics on the IPC or ordinary logs.


if __name__ == "__main__":
    raise SystemExit(main())
