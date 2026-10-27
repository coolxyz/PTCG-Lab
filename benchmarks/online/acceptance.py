"""Conservative per-sample P7-B checks, never a complete capacity certification."""

from hashlib import sha256
import json
from pathlib import Path
import re

from .identity import runtime_digest
from .measure import histogram_quantile

OPERATIONS = frozenset(
    {
        "session",
        "refresh",
        "deck",
        "room_create",
        "room_join",
        "room_ready",
        "snapshot",
        "initialization_wait",
        "control",
        "command",
        "receipt",
        "replay",
        "other",
    }
)


def read(path):
    return json.loads(Path(path).read_text())


def summarize(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root / "var/p7"):
        raise ValueError("PRIVATE_P7_DIRECTORY_REQUIRED")
    manifest = read(path / "lab.json")
    runs = list((path / "results").glob("load-*.json"))
    if len(runs) != 1:
        raise ValueError("ONE_COMPLETE_SAMPLE_REQUIRED")
    run, guard = read(runs[0]), read(path / "guard-report.json")
    measures = [
        p
        for p in (path / "measure").glob("*.json")
        if re.fullmatch(r"[a-f0-9]{32}\.json", p.name)
    ]
    if len(measures) != 1 or (path / "database.dump").read_bytes()[:5] != b"PGDMP":
        raise ValueError("COMPLETE_OBSERVER_AND_DATABASE_BACKUP_REQUIRED")
    observer = read(measures[0])
    before, after = (
        read(path / f"database-{label}.json") for label in ("before", "after")
    )
    samples = [
        json.loads(line)
        for line in (path / "resource-samples.jsonl").read_text().splitlines()
    ]
    steady = [r for r in samples if r["phase"] == "steady"]
    formal = run["label"] == "matrix"
    requests = run["requestOperations"]
    if not (
        run["replayReadConcurrency"] == 2
        and set(requests) == {"setup", "warmup", "steady", "fault", "cleanup"}
        and all(
            name in OPERATIONS
            and row["attempts"] == row["successes"] + row["pending"]
            and (row["pending"] == 0 or name == "initialization_wait")
            and row["errors"] == 0
            and row["firstAttemptErrors"] == []
            for phase in requests.values()
            for name, row in phase.items()
        )
        and sum(
            phase.get("command", {}).get("successes", 0) for phase in requests.values()
        )
        == sum(run["perGameCommands"])
    ):
        raise ValueError("REQUEST_STAGE_ERROR_OR_MISMATCH")
    waits_path = path / "measure/db-waits.json"
    if formal and waits_path.exists():
        raise ValueError("DIAGNOSTIC_SAMPLING_CANNOT_ENTER_FORMAL_MATRIX")
    repetition = (
        re.fullmatch(r"m(10|25|50|100)-w([12])-r([1-9][0-9]*)", path.name)
        if formal
        else None
    )
    if formal and (
        not repetition
        or int(repetition[1]) != run["matches"]
        or int(repetition[2]) != manifest["workers"]
    ):
        raise ValueError("FORMAL_SAMPLE_ID_REQUIRED")
    if not (
        run["status"] == "passed"
        and not run["failures"]
        and run["privateReplayVerified"] is True
        and manifest["runtimeSourceSha256"] == runtime_digest(root)
        and manifest["fixtureSeed"] is not None
        and manifest["settings"]["WORKER_CACHE_ENTRIES"] * manifest["workers"] == 16
        and run["matches"] in (10, 25, 50, 100)
        and run["players"] == 2 * run["matches"]
        and run["thinkSeconds"] == [5, 15]
        and all(not r["firstAttemptErrors"] for r in run["phases"].values())
        and guard["status"] == "passed"
        and guard["protectedServicesUnchanged"] is True
        and len(steady) >= 3
        and all(
            r["cpuMax"] == "200000 100000"
            and r["memoryMax"] == 2 * 1024**3
            and not r["swapBytes"]
            and not r["memoryEvents"]["oom_kill"]
            for r in samples
        )
        and after["fsync"]
        == after["synchronousCommit"]
        == after["trackWalIoTiming"]
        == "on"
        and before["proof"] == dict(matches=0, seq=0, receipts=0, frames=0)
    ):
        raise ValueError("INCOMPLETE_OR_INCONSISTENT_SAMPLE")
    if formal and not (
        run["formalSample"] is True
        and run["warmupSeconds"] >= 300
        and run["steadySeconds"] >= 1200
        and run["measuredSteadySeconds"] >= 1200
        and run["elapsedSeconds"] >= 1500
        and steady[-1]["time"] - steady[0]["time"] >= run["measuredSteadySeconds"] - 20
        and run["phases"]["steady"]["accepted"] >= 60 * run["matches"]
    ):
        raise ValueError("FORMAL_DURATION_REQUIRED")
    commands = sum(run["perGameCommands"])
    proof = after["proof"]
    if not (
        commands == proof["seq"] == proof["receipts"]
        and proof["frames"] == 2 * (commands + proof["matches"])
        and proof["matches"] >= run["matches"]
        and commands
        == sum(observer["phases"][p]["count"] for p in ("warmup", "steady"))
        and all(
            not (set(observer["responses"][p]) - {"200"}) for p in ("warmup", "steady")
        )
        and len(run["perGameCommands"]) == run["matches"]
        and all(n > 0 for n in run["perGameCommands"])
    ):
        raise ValueError("DURABLE_HISTORY_OR_FIRST_ATTEMPT_MISMATCH")
    elapsed = steady[-1]["time"] - steady[0]["time"]
    cpu = (steady[-1]["cpu"]["usage_usec"] - steady[0]["cpu"]["usage_usec"]) / 1e6
    p95 = histogram_quantile([observer["phases"]["steady"]], 0.95)
    p99 = histogram_quantile([observer["phases"]["steady"]], 0.99)
    cpu_percent = 100 * cpu / elapsed / 2
    targets = dict(
        latencyP95=p95 is not None and p95 <= 0.5,
        latencyP99=p99 is not None and p99 <= 1.5,
        averageCpu=cpu_percent <= 70,
        driverHeadroom=run["driverCpuSeconds"] / run["elapsedSeconds"] <= 0.7,
    )
    spans = observer.get("commandSpans", {}).get("steady", {})
    return dict(
        schema=1,
        formalSample=formal,
        capacityValidated=False,
        status="passed" if all(targets.values()) else "threshold_failed",
        targets=targets,
        workers=manifest["workers"],
        repeat=int(repetition[3]) if repetition else None,
        matches=run["matches"],
        players=run["players"],
        warmupSeconds=run["warmupSeconds"],
        steadySeconds=run["steadySeconds"],
        measuredSteadySeconds=run["measuredSteadySeconds"],
        elapsedSeconds=run["elapsedSeconds"],
        acceptedCommands=commands,
        steadyAccepted=run["phases"]["steady"]["accepted"],
        firstAttemptErrors=0,
        privateReplayVerified=True,
        durableHistoryVerified=True,
        protectedServicesUnchanged=True,
        imageId=manifest["imageId"],
        runtimeSourceSha256=manifest["runtimeSourceSha256"],
        initialFixtureSha256=after["initialFixtureSha256"],
        fixtureVersion="p7-fixture-v1",
        cacheEntriesTotal=16,
        ruleReleaseId=manifest["settings"]["APPROVED_RULE_RELEASE_ID"],
        targetVcpus=2,
        targetMemoryBytes=2 * 1024**3,
        swapBytes=0,
        oomKills=0,
        groupPeakMemoryBytes=max(r["memoryPeakBytes"] for r in samples),
        steadyAverageCpuPercentOfTwoVcpus=cpu_percent,
        asgiP95UpperSeconds=p95,
        asgiP99UpperSeconds=p99,
        clientHttp=run["phases"]["steady"]["httpRoundTripSeconds"],
        replayReadConcurrency=2,
        replayQueueWaitSeconds=run["replayQueueWaitSeconds"],
        requestOperations={
            p: {
                name: {
                    k: row[k] for k in ("attempts", "successes", "pending", "errors")
                }
                for name, row in rows.items()
            }
            for p, rows in requests.items()
        },
        bothVisible=run["phases"]["steady"]["bothPlayersVisibleSeconds"],
        commandSpans={
            k: dict(
                p95UpperSeconds=histogram_quantile([v], 0.95),
                maximumSeconds=v["maximum"],
            )
            for k, v in spans.items()
        },
        eventLoopLagMaximumSeconds=observer["eventLoopLag"]["maximum"],
        apiCpuStat={k: int(v) for k, v in observer["apiCpuStat"].items()},
        driverAverageCpuPercentOfOneVcpu=100
        * run["driverCpuSeconds"]
        / run["elapsedSeconds"],
        driverMaxRssKiB=run["driverMaxRssKiB"],
        databaseGrowthBytes=after["databaseBytes"] - before["databaseBytes"],
        walDelta={
            k: float(after["wal"][k]) - float(before["wal"][k])
            for k in (
                "wal_bytes",
                "wal_write",
                "wal_sync",
                "wal_write_time",
                "wal_sync_time",
            )
        },
        databaseWaitSampling=read(waits_path) if waits_path.exists() else None,
        sql=proof,
        evidenceSha256=sha256(
            b"".join(
                p.read_bytes()
                for p in [
                    runs[0],
                    measures[0],
                    path / "database-after.json",
                    path / "guard-report.json",
                ]
            )
        ).hexdigest(),
    )
