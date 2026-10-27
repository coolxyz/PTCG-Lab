# ruff: noqa: F811
"""Real SQL and concurrent HTTP requests exercise queue authority boundaries."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import time
import uuid

from fastapi.testclient import TestClient
import pytest
from ptcg_online_server.api import create_app
from ptcg_online_server.auth import Sessions
from ptcg_online_server.initialization import Initializer
from tests.online_v2.test_lobby import body, prepare
from tests.online_v2.test_matches import command, game  # noqa: F401


def wait(predicate):
    until = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < until
        time.sleep(0.005)


def headers(token, controller):
    return {"Authorization": "Bearer " + token, "X-PTCG-Controller": controller}


def block_worker(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = service.worker.command

    def blocked(*args):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(service.worker, "command", blocked)
    return entered, release, calls


def test_scheduled_http_duplicates_conflicts_and_durable_retry_bypass_full_queue(
    game, monkeypatch
):
    service, _, tokens, mid, controllers = game
    settings = replace(service.repo.settings, command_queue_per_match=1)
    app = create_app(settings, worker=service.worker, background=False)
    entered, release, calls = block_worker(service, monkeypatch)
    path = "/api/online/v2/matches/" + mid + "/commands"
    request = command(service.get(tokens[0], mid))
    with (
        TestClient(app, base_url=settings.public_origin) as client,
        ThreadPoolExecutor(max_workers=3) as threads,
    ):

        def submit(i, value):
            return client.post(
                path, headers=headers(tokens[i], controllers[i]), json=value
            )

        first = threads.submit(submit, 0, request)
        try:
            assert entered.wait(3)
            duplicate = threads.submit(submit, 0, request)
            wait(lambda: app.state.scheduler.waiters == 2)
            with service.repo.db() as db:
                assert (
                    db.execute(
                        "SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() AND state LIKE 'idle in transaction%'"
                    ).fetchone()["n"]
                    == 0
                )
            conflict = submit(0, {**request, "choice": {"optionId": "changed"}})
            assert (
                conflict.status_code == 409
                and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
            )
            rejected = submit(0, {**request, "commandId": uuid.uuid4().hex})
            assert rejected.status_code == 503 and rejected.json()["retryable"]
            release.set()
            result = first.result(timeout=3)
            assert (
                result.status_code == 200
                and duplicate.result(timeout=3).json() == result.json()
            )
            assert len(calls) == 1 and result.json()["matchSeq"] == 1
            # A stored receipt still succeeds with this match's one slot occupied.
            entered.clear()
            release.clear()
            next_request = command(service.get(tokens[1], mid))
            second = threads.submit(submit, 1, next_request)
            assert entered.wait(3)
            retried = submit(0, request)
            assert retried.status_code == 200 and retried.json() == result.json()
            release.set()
            assert second.result(timeout=3).status_code == 200
            assert len(calls) == 2
        finally:
            release.set()
    assert app.state.scheduler.task.done()
    assert all(v == 0 for v in app.state.scheduler.diagnostics().values())


def test_merged_caller_is_reauthorized_after_other_callers_commit(game, monkeypatch):
    service, _, tokens, mid, controllers = game
    sessions = Sessions(service.repo)
    owner = sessions.authenticate(tokens[0])
    follower = sessions.create(name="second-session", device_name="second-device")
    # Fixture creates a second authenticated device of the same user; no public
    # endpoint lets guests choose another user's identity.
    with service.repo.transaction() as db:
        device = uuid.uuid4().hex
        db.execute(
            "INSERT INTO ptcg_v2.devices VALUES(%s,%s,%s)",
            (device, owner["userId"], "second-owned-device"),
        )
        db.execute(
            "UPDATE ptcg_v2.sessions SET user_id=%s,device_id=%s WHERE id=%s",
            (owner["userId"], device, follower["sessionId"]),
        )
    app = create_app(service.repo.settings, worker=service.worker, background=False)
    entered, release, calls = block_worker(service, monkeypatch)
    request = command(service.get(tokens[0], mid))
    path = "/api/online/v2/matches/" + mid + "/commands"
    with (
        TestClient(app, base_url=service.repo.settings.public_origin) as client,
        ThreadPoolExecutor(max_workers=2) as threads,
    ):

        def submit(token):
            return client.post(
                path, headers=headers(token, controllers[0]), json=request
            )

        first = threads.submit(submit, tokens[0])
        try:
            assert entered.wait(3)
            second = threads.submit(submit, follower["accessToken"])
            wait(lambda: app.state.scheduler.waiters == 2)
            sessions.revoke(follower["accessToken"])
            release.set()
            assert first.result(timeout=3).status_code == 200
            assert second.result(timeout=3).status_code == 401
            assert len(calls) == 1 and service.get(tokens[0], mid)["matchSeq"] == 1
        finally:
            release.set()


@pytest.mark.parametrize("change", ["revoke", "takeover", "resign"])
def test_queued_match_rechecks_authority_and_version_before_rule_work(
    game, monkeypatch, change
):
    service, lobby, tokens, mid, controllers = game
    a, b, room, _ = prepare(lobby)
    assert Initializer(service.repo, service.worker).tick()
    assert service.promote_one()
    other = room["matchId"]
    controller = uuid.uuid4().hex
    service.control(a, other, body(controllerId=controller, expectedControllerEpoch=0))
    queued_request = command(service.get(a, other))
    entered, release, calls = block_worker(service, monkeypatch)
    app = create_app(service.repo.settings, worker=service.worker, background=False)
    with (
        TestClient(app, base_url=service.repo.settings.public_origin) as client,
        ThreadPoolExecutor(max_workers=2) as threads,
    ):
        first = threads.submit(
            client.post,
            "/api/online/v2/matches/" + mid + "/commands",
            headers=headers(tokens[0], controllers[0]),
            json=command(service.get(tokens[0], mid)),
        )
        try:
            assert entered.wait(3)
            second = threads.submit(
                client.post,
                "/api/online/v2/matches/" + other + "/commands",
                headers=headers(a, controller),
                json=queued_request,
            )
            wait(lambda: app.state.scheduler.diagnostics()["queued"] == 1)
            if change == "revoke":
                Sessions(service.repo).revoke(a)
            elif change == "takeover":
                service.control(
                    a,
                    other,
                    body(controllerId=uuid.uuid4().hex, expectedControllerEpoch=1),
                )
            else:
                service.resign(
                    a,
                    other,
                    dict(
                        protocolVersion="2.0",
                        commandId=uuid.uuid4().hex,
                        expectedMatchSeq=0,
                        expectedStateVersion=0,
                        controllerEpoch=1,
                    ),
                    controller,
                )
            release.set()
            assert first.result(timeout=3).status_code == 200
            result = second.result(timeout=3)
            assert (
                result.json()["code"]
                == {
                    "revoke": "UNAUTHORIZED",
                    "takeover": "CONTROL_LOST",
                    "resign": "MATCH_VERSION_CONFLICT",
                }[change]
            )
            assert len(calls) == 1  # Invalid queued work never reaches the rule worker.
            view = service.get(b, other)
            assert view["matchSeq"] == (1 if change == "resign" else 0)
        finally:
            release.set()


def test_scheduler_disabled_api_preserves_command_contract(game):
    service, _, tokens, mid, controllers = game
    settings = replace(service.repo.settings, command_queue_enabled=False)
    app = create_app(settings, worker=service.worker, background=False)
    with TestClient(app, base_url=settings.public_origin) as client:
        response = client.post(
            "/api/online/v2/matches/" + mid + "/commands",
            headers=headers(tokens[0], controllers[0]),
            json=command(service.get(tokens[0], mid)),
        )
        assert response.status_code == 200 and response.json()["accepted"]
        assert app.state.scheduler.task is None
