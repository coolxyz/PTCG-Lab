"""Replay fan-out respects server admission and failures stay visible and redacted."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
from types import SimpleNamespace

import pytest

from benchmarks.online import driver
from ptcg_online_client.contracts import ClientError
from ptcg_protocol.projection import replay_page, snapshot
from tests.protocol.test_projection import original


@pytest.fixture(autouse=True)
def fresh_driver(monkeypatch):
    monkeypatch.setattr(driver, "PHASE", "steady")
    monkeypatch.setattr(driver, "REPLAY_SLOTS", threading.BoundedSemaphore(2))
    monkeypatch.setattr(driver, "REPLAY_WAITS", [])
    monkeypatch.setattr(driver, "STOP", threading.Event())
    monkeypatch.setattr(
        driver,
        "REQUESTS",
        {p: {} for p in ("setup", "warmup", "steady", "fault", "cleanup")},
    )


def game(index=0):
    value = driver.Game.__new__(driver.Game)
    value.mid, value.index = f"synthetic-{index}", index
    value.tokens = [dict(accessToken=s) for s in ("PLAYER1", "PLAYER2")]
    value.controllers = ["synthetic-control"] * 2
    value.hashes, value.pages = [], {}
    for seat in ("PLAYER1", "PLAYER2"):
        frames = []
        for seq in (0, 1):
            view = original()
            view["stateVersion"] = seq
            if seat == "PLAYER2":
                view["observation"]["viewer"] = "player2"
                view["observation"]["self"]["id"] = "player2"
                view["observation"]["opponent"]["id"] = "player1"
                view["decision"] = None
            frames.append(
                snapshot(
                    view,
                    match_id=value.mid,
                    rule_release_id="synthetic-rules",
                    viewer_seat=seat,
                    match_seq=seq,
                    controller_epoch=0,
                    stream_cursor=f"cursor-{seq}",
                )
            )
        value.pages[seat] = [
            replay_page([frames[0]], next_cursor="page1"),
            replay_page([frames[1]]),
        ]
        value.hashes.append({str(f["matchSeq"]): driver.digest(f) for f in frames})
    return value


def test_ten_concurrent_replays_respect_two_server_slots_and_verify_all_pages():
    lock, entered, release = threading.Lock(), threading.Event(), threading.Event()
    active, maximum = 0, 0
    values = [game(i) for i in range(10)]

    class Transport:
        def __init__(self, g):
            self.game = g

        def request(self, method, path, data, *, token, controller):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    entered.set()
            try:
                if active > 2:
                    raise ClientError("CAPACITY_EXCEEDED", 503, retryable=True)
                assert release.wait(3)
                return deepcopy(self.game.pages[token][int(path.endswith("/page1"))])
            finally:
                with lock:
                    active -= 1

    for g in values:
        g.transport = Transport(g)
    with ThreadPoolExecutor(max_workers=10) as pool:
        jobs = [pool.submit(g.verify_replay) for g in values]
        try:
            assert entered.wait(3)
            with lock:
                assert active == 2
        finally:
            release.set()
        for job in jobs:
            job.result(timeout=5)
    assert maximum == 2 and active == 0
    row = driver.REQUESTS["steady"]["replay"]
    assert (row["attempts"], row["successes"], row["errors"]) == (40, 40, 0)
    assert len(driver.REPLAY_WAITS) == 40


def test_replay_rejection_is_not_retried_and_releases_its_slot():
    g = game()
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        raise ClientError("CAPACITY_EXCEEDED", 503, retryable=True)

    g.transport = SimpleNamespace(request=request)
    with pytest.raises(ClientError):
        g.verify_replay()
    assert len(calls) == 1
    row = driver.REQUESTS["steady"]["replay"]
    assert (
        row["errors"] == 1
        and row["firstAttemptErrors"][0]["code"] == "CAPACITY_EXCEEDED"
    )
    assert driver.REPLAY_SLOTS.acquire(blocking=False)
    assert driver.REPLAY_SLOTS.acquire(blocking=False)
    driver.REPLAY_SLOTS.release()
    driver.REPLAY_SLOTS.release()


def test_command_path_does_not_wait_for_replay_slots_or_hide_a_retry():
    g = game()
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        raise ClientError("NETWORK_UNAVAILABLE", retryable=True)

    g.transport = SimpleNamespace(request=request)
    driver.REPLAY_SLOTS.acquire()
    driver.REPLAY_SLOTS.acquire()
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(
            g.request,
            g.route + "/commands",
            i=0,
            data={"private": "never-log"},
            controller=True,
        )
        try:
            with pytest.raises(ClientError):
                job.result(timeout=1)
        finally:
            driver.REPLAY_SLOTS.release()
            driver.REPLAY_SLOTS.release()
    assert len(calls) == 1
    assert driver.REQUESTS["steady"]["command"]["errors"] == 1


def test_replay_cursor_stall_fails_instead_of_hanging_the_matrix():
    g = game()
    page = g.pages["PLAYER1"][0]
    g.transport = SimpleNamespace(request=lambda *a, **k: deepcopy(page))
    with pytest.raises(ValueError, match="REPLAY_CURSOR_STALLED"):
        g.verify_replay()


def test_failure_metadata_never_contains_exception_messages_or_paths():
    error = ClientError("https://secret-token.example/never-log", 503, retryable=True)
    value = driver.failure(error, "/matches/private-id/frames/private-cursor")
    assert value == dict(
        type="ClientError",
        operation="other",
        code="REMOTE_FAILED",
        status=503,
        retryable=True,
    )
    assert "secret-token" not in json.dumps(value) and "private-id" not in json.dumps(
        value
    )


def test_only_initialization_poll_403_is_counted_as_expected_pending():
    g = game()

    def forbidden(*a, **k):
        raise ClientError("FORBIDDEN", 403)

    g.transport = SimpleNamespace(request=forbidden)
    with pytest.raises(ClientError):
        g.request(g.route, i=0, initializing=True)
    row = driver.REQUESTS["steady"]["initialization_wait"]
    assert (row["attempts"], row["successes"], row["pending"], row["errors"]) == (
        1,
        0,
        1,
        0,
    )
    with pytest.raises(ClientError):
        g.request(g.route, i=0)
    assert driver.REQUESTS["steady"]["snapshot"]["errors"] == 1

    def busy(*a, **k):
        raise ClientError("CAPACITY_EXCEEDED", 503, retryable=True)

    g.transport = SimpleNamespace(request=busy)
    with pytest.raises(ClientError):
        g.request(g.route, i=0, initializing=True)
    assert row["errors"] == 1 and row["pending"] == 1
    with pytest.raises(ValueError):
        g.request(g.route + "/commands", initializing=True)


def test_initialization_poll_does_not_hide_a_permanent_failure(monkeypatch):
    g = game()
    g.inputs = {"release": "synthetic"}
    g.revisions = ["one", "two"]
    attempts = []

    def request(path, **kwargs):
        if kwargs.get("initializing"):
            attempts.append(1)
            raise ClientError("FORBIDDEN", 403)
        return dict(
            roomId="synthetic-room",
            inviteCode="synthetic",
            roomVersion=0,
            matchId=g.mid,
        )

    g.request = request
    clock = iter((0, 61))
    monkeypatch.setattr(driver.time, "monotonic", lambda: next(clock))
    with pytest.raises(ClientError):
        g.new_match()
    assert attempts == [1]


@pytest.mark.parametrize("cleanup", [False, True])
def test_load_keeps_structured_failures_even_outside_command_submission(
    monkeypatch, tmp_path, cleanup
):
    class Game:
        current_operation = "control"
        index = 0
        completed = commands = 0

        def __init__(self, *args):
            pass

        def setup(self):
            pass

        def save(self):
            pass

        def close(self):
            pass

        def step(self):
            if cleanup:
                driver.STOP.set()
            else:
                raise ClientError("CAPACITY_EXCEEDED", 503, retryable=True)

        def verify_replay(self):
            self.current_operation = "replay"
            raise ClientError("CAPACITY_EXCEEDED", 503, retryable=True)

    monkeypatch.setattr(driver, "Game", Game)
    monkeypatch.setattr(
        driver.random, "Random", lambda seed: SimpleNamespace(uniform=lambda *a: 0)
    )
    monkeypatch.setattr(driver, "phase", lambda p: setattr(driver, "PHASE", p))
    args = SimpleNamespace(
        label="pilot",
        matches=1,
        warmup=0,
        steady=1,
        state=str(tmp_path),
        seed=711,
        think_min=5,
        think_max=15,
        output=str(tmp_path / "result.json"),
    )
    with pytest.raises(SystemExit):
        driver.load(args, {})
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["status"] == "failed" and result["privateReplayVerified"] is False
    assert result["failures"] == [
        dict(
            type="ClientError",
            operation="replay" if cleanup else "control",
            code="CAPACITY_EXCEEDED",
            status=503,
            retryable=True,
        )
    ]
