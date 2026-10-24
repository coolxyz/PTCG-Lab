from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
import pytest
from ptcg_online_client.client import Client
from ptcg_online_client.contracts import ClientError, unpack
from ptcg_online_client.transport import Server
from ptcg_protocol.projection import snapshot
from tests.protocol.test_projection import original

MID = "a" * 32
SERVER = Server("https://api.example.test")


def tokens():
    return dict(
        protocolVersion="2.0",
        serverId="test",
        userId="1" * 32,
        deviceId="2" * 32,
        sessionId="3" * 32,
        tokenType="Bearer",
        accessToken="atk_" + "a" * 43,
        refreshToken="rtk_" + "a" * 43,
        accessExpiresAt=(
            datetime.now(timezone.utc) + timedelta(minutes=10)
        ).isoformat(),
        refreshExpiresAt=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    )


class API:
    def __init__(self):
        self.credentials = tokens()
        self.calls, self.receipts = [], {}
        self.drop = None
        self.view = snapshot(
            original(),
            match_id=MID,
            rule_release_id="synthetic-rules-1",
            viewer_seat="PLAYER1",
            match_seq=0,
            controller_epoch=0,
            stream_cursor="0" * 32,
        )

    def request(self, method, path, body=None, token=None, controller=None):
        self.calls.append((method, path, deepcopy(body), token))
        if path == "/capabilities":
            return dict(
                protocolVersion="2.0",
                supportedProtocolVersions=["2.0"],
                ruleReleaseIds=["synthetic-rules-1"],
                resources=dict(displayCatalogVersion="c", assetManifestVersion="a"),
                acceptingMatches=True,
                maxCommandBytes=16384,
                features=["invite", "duel", "private-replay", "reconnect"],
            )
        if path == "/sessions":
            return deepcopy(self.credentials)
        if path == "/sessions/refresh":
            assert body["refreshToken"] == self.credentials["refreshToken"]
            self.credentials.update(
                accessToken="atk_" + "b" * 43,
                refreshToken="rtk_" + "b" * 43,
                accessExpiresAt=(
                    datetime.now(timezone.utc) + timedelta(minutes=10)
                ).isoformat(),
            )
            if self.drop == "refresh":
                raise ClientError("NETWORK_UNAVAILABLE", retryable=True)
            return deepcopy(self.credentials)
        if path.endswith("/control"):
            self.view.update(controllerEpoch=1, streamCursor="1" * 32)
            return deepcopy(self.view)
        if "/receipts/" in path:
            key = path.rsplit("/", 1)[1]
            if key not in self.receipts:
                raise ClientError("INVALID_PAYLOAD", 404)
            return deepcopy(self.receipts[key])
        if path.endswith("/commands"):
            if self.drop == "before":
                raise ClientError("NETWORK_UNAVAILABLE", retryable=True)
            key = body["commandId"]
            if key not in self.receipts:
                self.view.update(matchSeq=1, stateVersion=1, streamCursor="2" * 32)
                self.receipts[key] = dict(
                    protocolVersion="2.0",
                    matchId=MID,
                    commandId=key,
                    accepted=True,
                    matchSeq=1,
                    stateVersion=1,
                    streamCursor="2" * 32,
                )
            if self.drop == "after":
                raise ClientError("NETWORK_UNAVAILABLE", retryable=True)
            return deepcopy(self.receipts[key])
        if path == "/matches/" + MID:
            return deepcopy(self.view)
        raise AssertionError("Unrecognized synthetic route")


def command():
    return dict(
        protocolVersion="2.0",
        commandId="command-1",
        expectedMatchSeq=0,
        expectedStateVersion=0,
        controllerEpoch=1,
        decisionId="d0",
        choice={"optionId": "d0:o0"},
    )


@pytest.fixture
def ready(tmp_path):
    api = API()
    client = Client(SERVER, tmp_path / "private", transport=api)
    client.create_guest()
    client.take_control(MID, 0)
    try:
        yield client, api, tmp_path / "private"
    finally:
        client.close()


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.test",
        "https://api.example.test/path",
        "https://x/?q=1",
        "https://u:p@x",
        "https://x#frag",
        "https://x:443",
        "https://X",
        "https://x\\y",
        "https://x:99999",
    ],
)
def test_reject_noncanonical_or_unsafe_origin(origin):
    with pytest.raises(ClientError, match="INVALID_SERVER"):
        Server(origin)


def test_private_file_permissions_profile_lock_and_server_isolation(ready):
    client, api, root = ready
    assert os.stat(root).st_mode & 0o777 == 0o700
    assert os.stat(client.vault.path).st_mode & 0o777 == 0o600
    with pytest.raises(ClientError, match="PROFILE_IN_USE"):
        Client(SERVER, root, transport=api)
    with Client(Server("https://other.example.test"), root, transport=api) as other:
        assert other.status()["identity"] is None
        assert other.vault.path != client.vault.path
    public = json.dumps(client.status())
    assert "atk_" not in public and "rtk_" not in public


@pytest.mark.parametrize("drop", ["before", "after"])
def test_lost_command_reply_survives_restart_and_reconciles_once(ready, drop):
    client, api, root = ready
    api.drop = drop
    with pytest.raises(ClientError, match="NETWORK_UNAVAILABLE"):
        client.submit(MID, command())
    with pytest.raises(ClientError, match="PENDING_COMMAND"):
        client.submit(MID, {**command(), "commandId": "new-id"})
    assert client.status()["pendingMatches"] == [MID]
    client.close()
    api.drop = None
    with Client(SERVER, root, transport=api) as resumed:
        assert resumed.recover(MID)["matchSeq"] == 1
        assert resumed.status()["pendingMatches"] == []
        assert len(api.receipts) == 1
        posts = [c for c in api.calls if c[1].endswith("/commands")]
        assert len(posts) == (1 if drop == "after" else 2)
        assert all(c[2] == command() for c in posts)


def expire(client):
    with client.vault.transaction() as state:
        state["credentials"]["accessExpiresAt"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        client.vault.save()


def test_parallel_callers_refresh_exactly_once(ready):
    client, api, _ = ready
    expire(client)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: client.snapshot(MID), range(8)))
    assert sum(c[1] == "/sessions/refresh" for c in api.calls) == 1


def test_lost_refresh_reply_never_reuses_consumed_token_after_restart(ready):
    client, api, root = ready
    expire(client)
    api.drop = "refresh"
    with pytest.raises(ClientError, match="REAUTHENTICATION_REQUIRED"):
        client.snapshot(MID)
    client.close()
    api.drop = None
    with Client(SERVER, root, transport=api) as resumed:
        with pytest.raises(ClientError, match="REAUTHENTICATION_REQUIRED"):
            resumed.snapshot(MID)
        assert resumed.status()["reauthenticationRequired"]
    assert sum(c[1] == "/sessions/refresh" for c in api.calls) == 1


def test_unknown_rejection_is_not_discarded_until_version_is_fenced(ready):
    client, api, _ = ready
    api.drop = "before"
    with pytest.raises(ClientError):
        client.submit(MID, command())
    with pytest.raises(ClientError, match="OUTCOME_UNKNOWN"):
        client.resolve_rejected(MID)
    api.view.update(controllerEpoch=2, streamCursor="3" * 32)
    assert client.resolve_rejected(MID) is None
    assert client.status()["pendingMatches"] == []


def test_unrecognized_rule_and_traversal_ids_never_send_requests(ready):
    client, api, _ = ready
    with pytest.raises(ClientError, match="RULE_RELEASE_UNAVAILABLE"):
        client.capabilities("other-rules")
    n = len(api.calls)
    with pytest.raises(ClientError, match="INVALID_ID"):
        client.snapshot("../sessions")
    assert len(api.calls) == n


@pytest.mark.parametrize(
    "payload", ['{"x":1,"x":2}', '{"x": NaN}', "[" * 40 + "0" + "]" * 40]
)
def test_invalid_wire_json_is_redacted(payload):
    with pytest.raises(ClientError, match="INVALID_RESPONSE"):
        unpack(payload)


def test_service_envelopes_are_exact_packaged_contracts():
    root = Path(__file__).resolve().parents[2]
    for file in (root / "apps/online-server/src/ptcg_online_server/schemas").glob(
        "*.json"
    ):
        assert (
            file.read_bytes()
            == (
                root
                / "packages/online-client/src/ptcg_online_client/schemas"
                / file.name
            ).read_bytes()
        )


def test_outbox_expiry_resyncs_without_emitting_old_snapshot(ready):
    client, api, _ = ready
    sent = []
    stop = threading.Event()

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def send(self, raw):
            sent.append(json.loads(raw))

        def recv(self, **kw):
            if len(sent) == 1:
                return json.dumps(
                    dict(protocolVersion="2.0", code="RESYNC_REQUIRED", retryable=False)
                )
            api.view.update(matchSeq=2, stateVersion=2, streamCursor="4" * 32)
            stop.set()
            return json.dumps(
                dict(protocolVersion="2.0", type="snapshot", snapshot=api.view)
            )

    api.stream = lambda _: Stream()
    events = list(client.events(MID, stop))
    assert sent[0]["streamCursor"] == "1" * 32 and sent[1]["streamCursor"] is None
    assert [e["snapshot"]["matchSeq"] for e in events] == [0, 2]
    assert client.vault.state["snapshots"][MID]["streamCursor"] == "4" * 32


def test_failed_journal_write_blocks_network_and_poisoned_owner(ready, monkeypatch):
    client, api, _ = ready

    def fail(*_):
        raise OSError("simulated disk failure")

    monkeypatch.setattr("ptcg_online_client.vault.os.replace", fail)
    with pytest.raises(ClientError, match="STORAGE_UNAVAILABLE"):
        client.submit(MID, command())
    assert not any(c[1].endswith("/commands") for c in api.calls)
    with pytest.raises(ClientError, match="STORAGE_UNAVAILABLE"):
        client.snapshot(MID)


def test_safe_get_retries_are_bounded_but_guest_creation_is_not_replayed(
    ready, monkeypatch
):
    client, api, _ = ready
    monkeypatch.setattr(client, "_backoff", lambda _: None)
    calls = []

    def fail(*args):
        calls.append(args)
        raise ClientError("NETWORK_UNAVAILABLE", retryable=True)

    monkeypatch.setattr(api, "request", fail)
    with pytest.raises(ClientError):
        client.capabilities()
    assert len(calls) == 3
    calls.clear()
    with pytest.raises(ClientError):
        client._request(
            "POST", "/sessions", {"protocolVersion": "2.0"}, authenticated=False
        )
    assert len(calls) == 1


@pytest.mark.parametrize("state", [[], {"schema": 1, "profile": SERVER.key}])
def test_corrupt_storage_is_rejected_without_creating_identity(tmp_path, state):
    root = tmp_path / "vault"
    root.mkdir(mode=0o700)
    path = root / (SERVER.key + ".json")
    path.write_text(json.dumps(state))
    path.chmod(0o600)
    with pytest.raises(ClientError, match="INVALID_STORAGE"):
        Client(SERVER, root, transport=API())


def test_http_snapshot_ahead_of_stream_resyncs_without_regression(ready):
    client, api, _ = ready
    sent = []
    stop = threading.Event()

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def send(self, raw):
            sent.append(json.loads(raw))

        def recv(self, **kw):
            old = deepcopy(api.view)
            api.view.update(matchSeq=2, stateVersion=2, streamCursor="4" * 32)
            if len(sent) == 1:
                client.snapshot(MID)  # A concurrent foreground HTTP read wins.
                view = old
            else:
                view = api.view
                stop.set()
            return json.dumps(
                dict(protocolVersion="2.0", type="snapshot", snapshot=view)
            )

    api.stream = lambda _: Stream()
    events = list(client.events(MID, stop))
    assert [e["snapshot"]["matchSeq"] for e in events] == [0, 2]
    assert sent[1]["streamCursor"] is None
