from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
import time
import pytest
from starlette.testclient import TestClient
from ptcg_desktop_api.api import create_app, PREFIX
from ptcg_desktop_api.bridge import Bridge, BridgeError
from ptcg_online_client.client import Client
from ptcg_online_client.contracts import ClientError
from tests.online_client.test_client import API, MID, SERVER, command

ORIGIN = "http://127.0.0.1:18785"


@pytest.fixture
def setup(tmp_path):
    remote = API()
    bridge = Bridge(
        {"test": SERVER},
        tmp_path / "private",
        client_factory=lambda s, p: Client(s, p, transport=remote),
    )
    token = bridge.issue_window()
    app = create_app(bridge, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 1234)) as web:
        yield bridge, remote, token, web


def post(setup, operation, body=None, *, token=None, headers=None):
    bridge, remote, default, web = setup
    return web.post(
        PREFIX + "/profiles/test/" + operation,
        json={} if body is None else body,
        headers={
            "Origin": ORIGIN,
            "X-PTCG-Bridge": token or default,
            **(headers or {}),
        },
    )


def prepare(setup):
    assert post(setup, "guest").status_code == 200
    result = post(setup, "control", {"matchId": MID, "expectedControllerEpoch": 0})
    assert result.status_code == 200
    return result.json()["result"]["lease"]


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://attacker.example"},
        {"Origin": "null"},
        {"Origin": ""},
        {"Host": "attacker.example"},
        {"Host": "localhost:18785"},
        {"X-PTCG-Bridge": "wrong"},
        {"X-PTCG-Bridge": ""},
        {"Sec-Fetch-Site": "cross-site"},
        {"Cookie": "remote=credential"},
        {"Authorization": "Bearer remote-secret"},
        {"X-Forwarded-For": "127.0.0.1"},
    ],
)
def test_untrusted_browser_never_creates_client_or_remote_identity(setup, headers):
    result = post(setup, "guest", headers=headers)
    assert result.status_code == 403
    assert setup[0].profiles["test"].client is None
    assert setup[1].calls == []
    assert "access-control-allow-origin" not in result.headers


def test_missing_origin_remote_peer_duplicate_origin_and_query_rejected(setup):
    owner, remote, token, web = setup
    url = PREFIX + "/profiles/test/guest"
    assert web.post(url, json={}, headers={"X-PTCG-Bridge": token}).status_code == 403
    assert (
        web.post(
            url,
            json={},
            headers=[("Origin", ORIGIN), ("Origin", ORIGIN), ("X-PTCG-Bridge", token)],
        ).status_code
        == 403
    )
    assert (
        web.post(
            url + "?token=ignored",
            json={},
            headers={"Origin": ORIGIN, "X-PTCG-Bridge": token},
        ).status_code
        == 403
    )
    other = TestClient(
        create_app(owner, origin=ORIGIN), base_url=ORIGIN, client=("10.0.0.9", 123)
    )
    assert (
        other.post(
            url, json={}, headers={"Origin": ORIGIN, "X-PTCG-Bridge": token}
        ).status_code
        == 403
    )
    assert remote.calls == []


def test_payload_and_routes_are_fixed_and_secret_free(setup):
    owner, remote, token, web = setup
    assert post(setup, "guest", {"url": "https://attacker.example"}).status_code == 400
    assert post(setup, "request", {"path": "/sessions/refresh"}).status_code == 404
    assert post(setup, "forget-identity").status_code == 404
    assert (
        post(
            setup, "deck", {"entries": [], "ruleReleaseId": "r", "requestId": ""}
        ).status_code
        == 400
    )
    assert remote.calls == []
    headers = {
        "Origin": ORIGIN,
        "X-PTCG-Bridge": token,
        "Content-Type": "application/json",
    }
    for raw in ('{"name":"a","name":"b"}', "[1]", "NaN"):
        assert (
            web.post(
                PREFIX + "/profiles/test/guest", content=raw, headers=headers
            ).status_code
            == 400
        )
    assert (
        web.post(
            PREFIX + "/profiles/test/guest", content=b"x" * 32769, headers=headers
        ).status_code
        == 413
    )
    assert (
        web.options(PREFIX + "/profiles/test/guest", headers=headers).status_code == 405
    )
    assert post(setup, "guest").status_code == 200
    status = post(setup, "status")
    assert status.status_code == 200 and status.headers["cache-control"] == "no-store"
    for secret in (
        remote.credentials["accessToken"],
        remote.credentials["refreshToken"],
    ):
        assert secret not in status.text
    assert "accessToken" not in status.text and "refreshToken" not in status.text


def test_multiwindow_takeover_fences_old_local_lease(setup):
    first = prepare(setup)
    second = setup[0].issue_window()
    result = post(
        setup, "control", {"matchId": MID, "expectedControllerEpoch": 1}, token=second
    )
    lease = result.json()["result"]["lease"]
    assert lease != first
    rejected = post(
        setup, "command", {"matchId": MID, "lease": first, "command": command()}
    )
    assert rejected.json()["error"]["code"] == "LOCAL_CONTROL_REQUIRED"
    assert not setup[1].receipts
    assert (
        post(
            setup,
            "command",
            {"matchId": MID, "lease": lease, "command": command()},
            token=second,
        ).status_code
        == 200
    )
    setup[0].revoke_window(second)
    assert post(setup, "status", token=second).status_code == 403
    assert post(setup, "status").status_code == 200


def test_pending_commit_recovers_after_new_bridge_owner(tmp_path):
    remote = API()

    def owner():
        return Bridge(
            {"test": SERVER},
            tmp_path / "private",
            client_factory=lambda s, p: Client(s, p, transport=remote),
        )

    bridge = owner()
    token = bridge.issue_window()
    bridge.call(token, "test", "guest", {})
    lease = bridge.call(
        token, "test", "control", {"matchId": MID, "expectedControllerEpoch": 0}
    )["lease"]
    remote.drop = "after"
    with pytest.raises(ClientError):
        bridge.call(
            token,
            "test",
            "command",
            {"matchId": MID, "lease": lease, "command": command()},
        )
    bridge.close()
    bridge = owner()
    try:
        token = bridge.issue_window()
        state = bridge.call(token, "test", "status", {})
        assert state["recovery"]["commands"] == [
            {"matchId": MID, "commandId": "command-1", "action": "commands"}
        ]
        assert "choice" not in json.dumps(state)
        remote.drop = None
        assert (
            bridge.call(token, "test", "recover", {"matchId": MID})["receipt"][
                "matchSeq"
            ]
            == 1
        )
        assert len(remote.receipts) == 1
        assert not bridge.call(token, "test", "status", {})["pendingMatches"]
    finally:
        bridge.close()


def test_shared_stream_bounded_latest_view_and_window_departure(setup):
    owner, remote, first, _ = setup
    prepare(setup)
    second = owner.issue_window()
    released = threading.Event()

    def events(mid, stop):
        try:
            for seq in range(100):
                view = deepcopy(remote.view)
                view.update(matchSeq=seq, stateVersion=seq)
                yield {"type": "update", "snapshot": view}
            stop.wait(5)
        finally:
            released.set()

    client = owner.profiles["test"].client
    client.events = events
    post(setup, "watch", {"matchId": MID})
    post(setup, "watch", {"matchId": MID}, token=second)
    assert len(owner.feeds) == 1
    for _ in range(100):
        value = post(setup, "events", {"matchId": MID}, token=second).json()["result"]
        if value["revision"] == 100:
            break
        time.sleep(0.01)
    assert value["event"]["snapshot"]["matchSeq"] == 99
    post(setup, "unwatch", {"matchId": MID})
    assert not released.is_set()
    owner.revoke_window(second)
    assert released.wait(2)
    assert not remote.receipts  # Closing display never resigns.


def test_profile_busy_does_not_block_other_profile(tmp_path):
    from ptcg_online_client.transport import Server

    remote = API()
    bridge = Bridge(
        {"one": SERVER, "two": Server("https://other.example.test")},
        tmp_path / "private",
        client_factory=lambda s, p: Client(s, p, transport=remote),
    )
    entered, release = threading.Event(), threading.Event()
    try:
        token = bridge.issue_window()
        bridge.call(token, "one", "guest", {})
        original = bridge.profiles["one"].client.capabilities

        def slow():
            entered.set()
            release.wait(5)
            return original()

        bridge.profiles["one"].client.capabilities = slow
        with ThreadPoolExecutor() as pool:
            task = pool.submit(bridge.call, token, "one", "capabilities", {})
            assert entered.wait(2)
            with pytest.raises(BridgeError, match="PROFILE_BUSY"):
                bridge.call(token, "one", "status", {})
            assert bridge.call(token, "two", "status", {})["identity"] is None
            release.set()
            assert task.result()["protocolVersion"] == "2.0"
    finally:
        release.set()
        bridge.close()


def test_expired_window_and_abandoned_feed_are_stopped(setup):
    owner, _, token, _ = setup
    with owner.lock:
        owner.windows[token] = time.monotonic() - 1
    assert post(setup, "guest").status_code == 403
    assert not owner.windows


def test_unexpected_errors_never_expose_exception_or_secret(setup):
    prepare(setup)

    def fail():
        raise ValueError("credential=" + setup[1].credentials["refreshToken"])

    setup[0].profiles["test"].client.capabilities = fail
    result = post(setup, "capabilities")
    assert result.status_code == 500
    assert result.json()["error"]["code"] == "BRIDGE_FAILED"
    assert "credential" not in result.text


def test_unknown_control_reply_can_be_recovered_and_old_window_is_fenced(setup):
    first = prepare(setup)
    owner, remote, _, _ = setup
    original = remote.request

    def lose(method, path, *args):
        result = original(method, path, *args)
        if path.endswith("/control"):
            raise ClientError("NETWORK_UNAVAILABLE", retryable=True)
        return result

    remote.request = lose
    second = owner.issue_window()
    result = post(
        setup, "control", {"matchId": MID, "expectedControllerEpoch": 1}, token=second
    )
    assert result.json()["error"]["code"] == "NETWORK_UNAVAILABLE"
    assert (
        post(
            setup, "command", {"matchId": MID, "lease": first, "command": command()}
        ).json()["error"]["code"]
        == "LOCAL_CONTROL_REQUIRED"
    )
    state = post(setup, "status").json()["result"]
    assert state["recovery"]["controls"] == [
        {"matchId": MID, "expectedControllerEpoch": 1}
    ]
    assert "controllerId" not in json.dumps(state)
    remote.request = original
    result = post(setup, "recover-control", {"matchId": MID}, token=second)
    assert result.status_code == 200
    assert not post(setup, "status").json()["result"]["recovery"]["controls"]


def test_idle_feed_is_reaped_without_more_browser_requests(setup):
    owner, _, token, _ = setup
    prepare(setup)
    stopped = threading.Event()

    def events(mid, stop):
        stop.wait(4)
        stopped.set()
        yield from ()

    owner.profiles["test"].client.events = events
    post(setup, "watch", {"matchId": MID})
    with owner.lock:
        next(iter(owner.feeds.values())).watchers[token] = time.monotonic() - 1
    assert stopped.wait(2)


def test_global_admission_and_chunked_body_limit(setup):
    import asyncio
    from ptcg_desktop_api.api import BridgeAPI

    owner, remote, token, _ = setup
    api = BridgeAPI(owner, ORIGIN)
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": PREFIX + "/profiles/test/guest",
        "raw_path": b"/api/desktop/online/v1/profiles/test/guest",
        "query_string": b"",
        "client": ("127.0.0.1", 1),
        "headers": [
            (b"host", b"127.0.0.1:18785"),
            (b"origin", ORIGIN.encode()),
            (b"x-ptcg-bridge", token.encode()),
            (b"content-type", b"application/json"),
        ],
    }

    async def run():
        messages = []

        async def send(message):
            messages.append(message)

        blocked = asyncio.Event()

        async def receive():
            await blocked.wait()
            return {"type": "http.request", "body": b"{}"}

        tasks = [asyncio.create_task(api(scope, receive, send)) for _ in range(4)]
        await asyncio.sleep(0.01)
        await api(scope, receive, send)
        assert messages[0]["status"] == 503
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert api.active == 0
        messages.clear()
        chunks = iter([b"x" * 16384, b"x" * 16384, b"x"])

        async def chunk():
            return {"type": "http.request", "body": next(chunks), "more_body": True}

        await api(scope, chunk, send)
        assert messages[0]["status"] == 413
        assert remote.calls == []

    asyncio.run(run())


def test_shutdown_deadline_keeps_busy_credential_owner_locked(setup):
    owner, _, token, _ = setup
    prepare(setup)
    profile = owner.profiles["test"]
    profile.lock.acquire()
    try:
        with pytest.raises(BridgeError, match="SHUTDOWN_PENDING"):
            owner.close(timeout=0)
        assert profile.client.vault.closed is False
        with pytest.raises(BridgeError, match="BRIDGE_CLOSED"):
            owner.authorize(token)
    finally:
        profile.lock.release()
    owner.close()
    assert profile.client.vault.closed
