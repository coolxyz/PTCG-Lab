"""A matrix sample must prove wall time and durable history, not just a label."""

import json

import pytest

from benchmarks.online import acceptance
from benchmarks.online.measure import Histogram, latency


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "runtime_digest", lambda root: "runtime")
    path = tmp_path / "var/p7/m10-w1-r1"
    for name in ("results", "measure"):
        (path / name).mkdir(parents=True)

    def write(name, value):
        (path / name).write_text(json.dumps(value))

    write(
        "lab.json",
        dict(
            runtimeSourceSha256="runtime",
            fixtureSeed=1,
            workers=1,
            settings=dict(WORKER_CACHE_ENTRIES=16, APPROVED_RULE_RELEASE_ID="test"),
            imageId="test",
        ),
    )
    h = Histogram()
    for _ in range(1000):
        h.observe(0.4)
    empty = Histogram().snapshot()
    write(
        "results/load-one.json",
        dict(
            status="passed",
            failures=[],
            label="matrix",
            formalSample=True,
            privateReplayVerified=True,
            matches=10,
            players=20,
            thinkSeconds=[5, 15],
            warmupSeconds=300,
            steadySeconds=1200,
            measuredSteadySeconds=1200,
            elapsedSeconds=1500,
            perGameCommands=[100] * 10,
            driverCpuSeconds=10,
            driverMaxRssKiB=1000,
            replayReadConcurrency=2,
            replayQueueWaitSeconds=latency([]),
            requestOperations={
                p: (
                    {
                        "command": dict(
                            attempts=1000,
                            successes=1000,
                            pending=0,
                            errors=0,
                            firstAttemptErrors=[],
                        )
                    }
                    if p == "steady"
                    else {}
                )
                for p in ("setup", "warmup", "steady", "fault", "cleanup")
            },
            phases=dict(
                warmup=dict(firstAttemptErrors={}),
                steady=dict(
                    firstAttemptErrors={},
                    accepted=1000,
                    httpRoundTripSeconds=latency([0.4]),
                    bothPlayersVisibleSeconds=latency([0.45]),
                ),
            ),
        ),
    )
    write("guard-report.json", dict(status="passed", protectedServicesUnchanged=True))
    write(
        "measure/00000000000000000000000000000000.json",
        dict(
            phases=dict(warmup=empty, steady=h.snapshot()),
            responses=dict(warmup={}, steady={"200": 1000}),
            eventLoopLag=empty,
            apiCpuStat={},
        ),
    )
    wal = dict.fromkeys(
        ("wal_bytes", "wal_write", "wal_sync", "wal_write_time", "wal_sync_time"), 1
    )
    write(
        "database-before.json",
        dict(
            databaseBytes=100,
            wal=wal,
            proof=dict(matches=0, seq=0, receipts=0, frames=0),
        ),
    )
    write(
        "database-after.json",
        dict(
            databaseBytes=200,
            wal=wal,
            fsync="on",
            synchronousCommit="on",
            trackWalIoTiming="on",
            initialFixtureSha256="fixture",
            proof=dict(matches=10, seq=1000, receipts=1000, frames=2020),
        ),
    )
    rows = [
        dict(
            phase="steady",
            time=t,
            cpuMax="200000 100000",
            memoryMax=2 * 1024**3,
            swapBytes=0,
            memoryEvents=dict(oom_kill=0),
            cpu=dict(usage_usec=t * 1000),
            memoryPeakBytes=1000,
        )
        for t in (0, 600, 1200)
    ]
    (path / "resource-samples.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (path / "database.dump").write_bytes(b"PGDMP")
    return tmp_path, path


@pytest.mark.parametrize(
    "field,value",
    [
        ("warmupSeconds", 299),
        ("steadySeconds", 1199),
        ("measuredSteadySeconds", 1199.9),
        ("elapsedSeconds", 1499),
    ],
)
def test_configured_or_actual_short_sample_cannot_enter_formal_matrix(
    sample, field, value
):
    root, path = sample
    target = path / "results/load-one.json"
    run = json.loads(target.read_text())
    run[field] = value
    target.write_text(json.dumps(run))
    with pytest.raises(ValueError, match="FORMAL_DURATION"):
        acceptance.summarize(path, root)


def test_missing_durable_receipt_or_frame_fails_even_with_successful_http(sample):
    root, path = sample
    target = path / "database-after.json"
    db = json.loads(target.read_text())
    db["proof"]["receipts"] -= 1
    target.write_text(json.dumps(db))
    with pytest.raises(ValueError, match="DURABLE_HISTORY"):
        acceptance.summarize(path, root)


def test_p95_target_failure_remains_a_failed_sample_with_no_capacity_claim(sample):
    root, path = sample
    target = path / "measure/00000000000000000000000000000000.json"
    observer = json.loads(target.read_text())
    h = Histogram()
    for _ in range(1000):
        h.observe(0.6)
    observer["phases"]["steady"] = h.snapshot()
    target.write_text(json.dumps(observer))
    result = acceptance.summarize(path, root)
    assert result["status"] == "threshold_failed"
    assert result["targets"]["latencyP95"] is False
    assert result["formalSample"] is True and result["capacityValidated"] is False


def test_resource_samples_must_cover_the_measured_steady_interval(sample):
    root, path = sample
    target = path / "resource-samples.jsonl"
    rows = [json.loads(line) for line in target.read_text().splitlines()]
    for r in rows:
        r["time"] /= 100
    target.write_text("\n".join(json.dumps(r) for r in rows))
    with pytest.raises(ValueError, match="FORMAL_DURATION"):
        acceptance.summarize(path, root)


def test_replay_first_attempt_error_cannot_hide_behind_successful_commands(sample):
    root, path = sample
    target = path / "results/load-one.json"
    run = json.loads(target.read_text())
    run["requestOperations"]["cleanup"]["replay"] = dict(
        attempts=1,
        pending=0,
        successes=0,
        errors=1,
        firstAttemptErrors=[dict(code="CAPACITY_EXCEEDED", status=503, retryable=True)],
    )
    target.write_text(json.dumps(run))
    with pytest.raises(ValueError, match="REQUEST_STAGE_ERROR"):
        acceptance.summarize(path, root)


def test_replay_admission_configuration_is_part_of_the_sample_contract(sample):
    root, path = sample
    target = path / "results/load-one.json"
    run = json.loads(target.read_text())
    run["replayReadConcurrency"] = 3
    target.write_text(json.dumps(run))
    with pytest.raises(ValueError, match="REQUEST_STAGE_ERROR"):
        acceptance.summarize(path, root)
