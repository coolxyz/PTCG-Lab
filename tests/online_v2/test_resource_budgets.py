# ruff: noqa: F811
"""Independent transport, replay, send and maintenance budgets."""

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb
from ptcg_online_server.api import Boundary
from ptcg_online_server.auth import Sessions
from ptcg_online_server.budgets import SendBudget, wire_size
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.metrics import Metrics
from ptcg_online_server.repository import Repository
from ptcg_online_server.stream import Streams
from tests.online_v2.test_boundary import config
from tests.online_v2.test_matches import game, command  # noqa: F401


def test_replay_inflight_reserves_http_capacity_and_websockets_do_not_consume_it():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        repo = SimpleNamespace(metrics=Metrics(), rate=lambda *a, **k: None)
        seen = []

        async def app(scope, receive, send):
            if scope["path"].endswith("frames"):
                entered.set()
                await release.wait()
            if scope["type"] == "websocket":
                seen.append("websocket")
                return
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        boundary = Boundary(
            app, replace(config(), max_inflight=2, replay_inflight=1), repo
        )

        async def invoke(path, kind="http"):
            out = []

            async def receive():
                return {"type": "http.request", "body": b""}

            async def send(message):
                if message["type"] == "http.response.start":
                    out.append(message["status"])

            await boundary(
                dict(
                    type=kind,
                    method="GET",
                    path=path,
                    headers=[(b"host", b"127.0.0.1:8784")],
                ),
                receive,
                send,
            )
            return out

        task = asyncio.create_task(invoke("/api/online/v2/matches/test/frames"))
        await entered.wait()
        assert await invoke("/api/online/v2/matches/test/timeline") == [503]
        assert await invoke("/api/online/v2/matches/test") == [200]
        assert await invoke("/api/online/v2/stream", "websocket") == []
        assert seen == ["websocket"] and boundary.inflight == boundary.replays == 1
        release.set()
        assert await task == [200]
        assert boundary.inflight == boundary.replays == 0

    asyncio.run(run())


def test_transport_ceiling_includes_http_streams_and_keepalive_reserve(monkeypatch):
    from ptcg_online_server import __main__ as entry

    settings = replace(config(), max_inflight=3, max_streams=7, connection_reserve=2)
    monkeypatch.setattr(entry.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(entry, "create_app", lambda _: "app")
    calls = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **k: calls.append(k))
    assert entry.main() == 0
    assert calls[0]["limit_concurrency"] == 12
    assert calls[0]["proxy_headers"] is False and calls[0]["ws_max_queue"] == 2


def test_send_burst_rate_and_oversize_are_bounded_without_retaining_payloads():
    now = [0.0]
    budget = SendBudget(1024, 4096, clock=lambda: now[0])
    assert budget.delay(4096, 1) == 0
    assert budget.delay(512, 1) == 0.5
    with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
        budget.delay(2048, 1)
    now[0] = 10
    assert budget.delay(4096, 1) == 0
    with pytest.raises(ServiceError):
        budget.delay(4097, 3)
    assert set(vars(budget)) == {
        "rate",
        "burst",
        "clock",
        "subscription",
        "authorize",
        "tokens",
        "updated",
    }


def test_throttled_stream_rechecks_revocation_and_keeps_small_error_allowance():
    async def run():
        repo = SimpleNamespace(metrics=Metrics())
        streams = Streams(
            replace(config(), stream_message_bytes=4096, stream_page_bytes=4096),
            SimpleNamespace(repo=repo),
        )
        sent = []

        async def send_json(value):
            sent.append(value)

        peer = SimpleNamespace(send_json=send_json)
        budget = SendBudget(1024 * 1024, 4096)
        budget.tokens = 0

        def revoked():
            raise ServiceError("UNAUTHORIZED", 401)

        budget.authorize = revoked
        streams.budgets[id(peer)] = budget
        with pytest.raises(ServiceError, match="UNAUTHORIZED"):
            await streams.send(peer, {"snapshot": "x" * 3000})
        assert not sent
        await streams.send(peer, ServiceError("UNAUTHORIZED", 401).payload())
        assert sent[0]["code"] == "UNAUTHORIZED"
        with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
            await streams.send(peer, {"snapshot": "x" * 4096})

    asyncio.run(run())


def test_handshake_admission_releases_every_slot_on_cancel():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def never():
            entered.set()
            await release.wait()

        class Peer:
            scope = {
                "headers": [
                    (b"host", b"127.0.0.1:8784"),
                    (b"authorization", b"Bearer synthetic"),
                ]
            }

            def __init__(self):
                self.closed = []

            async def accept(self):
                pass

            async def close(self, code):
                self.closed.append(code)

        service = SimpleNamespace(
            repo=SimpleNamespace(rate=lambda *_: None),
            sessions=SimpleNamespace(authenticate=lambda _: None),
        )
        streams = Streams(replace(config(), stream_handshakes=1), service)

        async def request(*_):
            await never()

        streams.request = request
        first, second = Peer(), Peer()
        task = asyncio.create_task(streams.serve(first))
        await entered.wait()
        await streams.serve(second)
        assert second.closed == [1013] and streams.opening == streams.active == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert streams.active == streams.opening == 0 and streams.budgets == {}

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["peer", "global"])
def test_replay_rate_is_durable_and_does_not_exhaust_command_allowance(repo, kind):
    settings = replace(
        repo.settings,
        replay_per_minute=1 if kind == "peer" else 100,
        replay_global_per_minute=1 if kind == "global" else 100,
    )
    limited = Repository(settings)
    limited.rate("first", replaying=True)
    for peer in ["first"] if kind == "peer" else ["second", "third"]:
        with pytest.raises(ServiceError) as error:
            Repository(settings).rate(peer, replaying=True)
        assert error.value.status == 429
    limited.rate("first")  # Foreground has its own remaining allowance.


def test_replay_and_stream_byte_pages_preserve_cursors_and_private_history(game):
    service, _, tokens, mid, controllers = game
    initial_cursor = service.get(tokens[0], mid)["streamCursor"]
    for step in range(4):
        i = step % 2
        service.command(
            tokens[i], mid, command(service.get(tokens[i], mid)), controllers[i]
        )
    with service.repo.db() as db:
        for table in ("match_frames", "match_outbox"):
            # Original synthetic padding, never real card or credential data.
            rows = db.execute(
                f"SELECT cursor,snapshot FROM ptcg_v2.{table} WHERE match_id=%s", (mid,)
            ).fetchall()
            for row in rows:
                frame = row["snapshot"]
                side = frame["observation"]["self"]
                side["hand"] = [
                    {**side["hand"][0], "name": side["hand"][0]["name"] + "x" * 150}
                    for _ in range(20)
                ]
                side["handCount"] = 20
                db.execute(
                    f"UPDATE ptcg_v2.{table} SET snapshot=%s WHERE cursor=%s",
                    (Jsonb(frame), row["cursor"]),
                )
        maximum = db.execute(
            "SELECT max(octet_length(snapshot::text)) AS n FROM ptcg_v2.match_outbox WHERE match_id=%s",
            (mid,),
        ).fetchone()["n"]
    limit = max(4096, maximum + 1280)
    service.repo.settings = replace(
        service.repo.settings,
        replay_page_bytes=limit,
        stream_page_bytes=limit,
        stream_message_bytes=limit,
    )
    frames, cursor, first_cursor = [], None, None
    while True:
        page = service.frames(tokens[0], mid, cursor)
        assert wire_size(page) <= limit and len(page["frames"]) == 1
        frames += page["frames"]
        cursor = page["nextCursor"]
        if first_cursor is None:
            first_cursor = cursor
        if cursor is None:
            break
    assert [f["matchSeq"] for f in frames] == list(range(5))
    assert "private-PLAYER2" not in json.dumps(frames)
    cursor, received = initial_cursor, []
    while True:
        page = service.stream(tokens[0], mid, cursor)
        assert wire_size(page) <= limit and len(page) == 1
        received += [e["snapshot"]["matchSeq"] for e in page]
        cursor = page[-1]["snapshot"]["streamCursor"]
        if not page.more:
            break
    assert received == [1, 2, 3, 4]
    with pytest.raises(ServiceError, match="RESYNC_REQUIRED"):
        service.frames(tokens[1], mid, first_cursor)
    service.repo.settings = replace(
        service.repo.settings,
        replay_page_bytes=4096,
        stream_page_bytes=4096,
        stream_message_bytes=4096,
    )
    with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
        service.frames(tokens[0], mid)
    assert service.get(tokens[0], mid)["matchSeq"] == 4


def test_maintenance_batches_expiry_without_deleting_replay_or_refresh_history(game):
    service, _, _, mid, _ = game
    repo = service.repo
    repo.settings = replace(repo.settings, maintenance_batch=2)
    for _ in range(3):
        Sessions(repo).create(name="expiry", device_name="expiry")
    with repo.db() as db:
        db.execute(
            "UPDATE ptcg_v2.access_tokens SET expires_at=now()-interval '1 minute'"
        )
        for i in range(5):
            db.execute(
                "INSERT INTO ptcg_v2.rate_buckets VALUES(%s,1,now()-interval '1 minute')",
                (f"expired-{i}",),
            )
            db.execute(
                "INSERT INTO ptcg_v2.rooms(id,invite_code,rule_release_id,as_of,status,expires_at) VALUES(%s,%s,'test','test','waiting',now()-interval '1 minute')",
                (f"expired-{i}", f"expired-{i}"),
            )
        before = [
            db.execute(f"SELECT count(*) AS n FROM ptcg_v2.{table}").fetchone()["n"]
            for table in ("access_tokens", "refresh_tokens", "match_frames")
        ]
    repo.maintain()
    with repo.db() as db:
        after = [
            db.execute(f"SELECT count(*) AS n FROM ptcg_v2.{table}").fetchone()["n"]
            for table in ("access_tokens", "refresh_tokens", "match_frames")
        ]
        assert after == [before[0] - 2, before[1], before[2]]
        assert (
            db.execute(
                "SELECT count(*) AS n FROM ptcg_v2.rate_buckets WHERE key LIKE 'expired-%%'"
            ).fetchone()["n"]
            == 3
        )
        assert (
            db.execute(
                "SELECT count(*) AS n FROM ptcg_v2.rooms WHERE status='closed'"
            ).fetchone()["n"]
            == 2
        )
        assert (
            db.execute(
                "SELECT status FROM ptcg_v2.matches WHERE id=%s", (mid,)
            ).fetchone()["status"]
            == "playing"
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("worker_weighted_enabled", 1),
        ("worker_hot_weight", 0),
        ("stream_handshakes", 0),
        ("stream_page_bytes", 1024),
        ("stream_message_bytes", 3 * 1024 * 1024),
        ("stream_burst_bytes", 4096),
        ("replay_inflight", 0),
        ("replay_per_minute", 0),
        ("maintenance_batch", 257),
        ("maintenance_interval", float("nan")),
        ("maintenance_sql_seconds", 6),
        ("stream_budget_wait", True),
    ],
)
def test_resource_settings_reject_unbounded_or_ambiguous_values(field, value):
    with pytest.raises(ValueError, match="INVALID_RESOURCE_BUDGET"):
        replace(config(), **{field: value})


def test_visible_reads_do_not_load_authoritative_recovery(game, monkeypatch):
    service, _, tokens, mid, _ = game
    queries = []
    execute = service.repo.connection_class.execute

    def record(db, sql, *args, **kwargs):
        queries.append(sql)
        return execute(db, sql, *args, **kwargs)

    monkeypatch.setattr(service.repo.connection_class, "execute", record)
    current = service.get(tokens[0], mid)
    service.frames(tokens[0], mid)
    service.stream(tokens[0], mid, current["streamCursor"])
    projections = [
        q.split(" FROM ")[0] for q in queries if "FROM ptcg_v2.matches " in q
    ]
    assert len(projections) == 3
    assert all("recovery" not in q and "*" not in q for q in projections)


def test_stream_budget_exhaustion_reports_error_and_reconnect_uses_durable_cursor(game):
    import time
    from fastapi.testclient import TestClient
    from ptcg_online_server.api import create_app

    service, _, tokens, mid, controllers = game
    settings = replace(
        service.repo.settings,
        stream_message_bytes=4096,
        stream_page_bytes=4096,
        stream_burst_bytes=4096,
        stream_bytes_per_second=1024,
        stream_budget_wait=0.001,
    )
    app = create_app(settings, worker=service.worker, background=False)
    headers = {"Authorization": "Bearer " + tokens[0], "Host": settings.host}
    with TestClient(app, base_url=settings.public_origin) as client:
        with client.websocket_connect("/api/online/v2/stream", headers=headers) as ws:
            ws.send_json(dict(protocolVersion="2.0", matchId=mid, streamCursor=None))
            first = ws.receive_json()["snapshot"]
            # Deterministically exhaust this connection's byte allowance.
            budget = next(iter(app.state.streams.budgets.values()))
            budget.tokens = -4096
            budget.updated = time.monotonic()
            service.command(tokens[0], mid, command(first), controllers[0])
            assert ws.receive_json()["code"] == "CAPACITY_EXCEEDED"
        with client.websocket_connect("/api/online/v2/stream", headers=headers) as ws:
            ws.send_json(
                dict(
                    protocolVersion="2.0",
                    matchId=mid,
                    streamCursor=first["streamCursor"],
                )
            )
            event = ws.receive_json()
            assert event["type"] == "update" and event["snapshot"]["matchSeq"] == 1
            assert "private-PLAYER2" not in json.dumps(event)
    assert app.state.streams.active == app.state.streams.opening == 0
    assert not app.state.streams.budgets and not app.state.streams.hub.entries


def test_maintenance_skips_locked_expired_rows(repo):
    repo.settings = replace(repo.settings, maintenance_batch=1)
    with repo.db() as db:
        for key in ("locked", "free"):
            db.execute(
                "INSERT INTO ptcg_v2.rate_buckets VALUES(%s,1,now()-interval '1 minute')",
                (key,),
            )
    with repo.db() as holder:
        holder.execute(
            "SELECT key FROM ptcg_v2.rate_buckets WHERE key='locked' FOR UPDATE"
        )
        repo.maintain()
        rows = holder.execute(
            "SELECT key FROM ptcg_v2.rate_buckets ORDER BY key"
        ).fetchall()
        assert rows == [{"key": "locked"}]


def test_weighted_environment_rollback_is_explicit(monkeypatch):
    from ptcg_online_server.config import Settings

    monkeypatch.delenv("PTCG_ONLINE_V2_DATABASE_URL_FILE", raising=False)
    monkeypatch.setenv("PTCG_ONLINE_V2_DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("PTCG_ONLINE_V2_SERVER_ID", "budget-test")
    monkeypatch.setenv("PTCG_ONLINE_V2_PUBLIC_ORIGIN", "https://example.test")
    monkeypatch.setenv("PTCG_ONLINE_V2_WORKER_WEIGHTED_ENABLED", "0")
    assert Settings.from_env().worker_weighted_enabled is False
    monkeypatch.setenv("PTCG_ONLINE_V2_WORKER_WEIGHTED_ENABLED", "true")
    with pytest.raises(ValueError, match="INVALID_DEPLOYMENT_CONFIG"):
        Settings.from_env()


def test_expired_rate_key_resets_even_when_cleanup_batch_cannot_reach_it(repo):
    from hashlib import sha256

    repo.settings = replace(repo.settings, maintenance_batch=1, requests_per_minute=1)
    key = "peer:" + sha256((repo.settings.server_id + ":peer").encode()).hexdigest()
    with repo.db() as db:
        db.execute(
            "INSERT INTO ptcg_v2.rate_buckets VALUES('oldest',1,now()-interval '2 minutes')"
        )
        db.execute(
            "INSERT INTO ptcg_v2.rate_buckets VALUES(%s,10,now()-interval '1 minute')",
            (key,),
        )
    repo.rate("peer")
    with repo.db() as db:
        assert (
            db.execute(
                "SELECT count FROM ptcg_v2.rate_buckets WHERE key=%s", (key,)
            ).fetchone()["count"]
            == 1
        )
    with pytest.raises(ServiceError) as error:
        repo.rate("peer")
    assert error.value.status == 429


def test_cleanup_cannot_remove_rate_bucket_between_read_and_increment(
    repo, monkeypatch
):
    from contextlib import contextmanager
    from hashlib import sha256

    repo.settings = replace(repo.settings, maintenance_batch=1, requests_per_minute=1)
    key = "peer:" + sha256((repo.settings.server_id + ":peer").encode()).hexdigest()
    with repo.db() as db:
        db.execute(
            "INSERT INTO ptcg_v2.rate_buckets VALUES('oldest',1,now()-interval '2 minutes')"
        )
        db.execute(
            "INSERT INTO ptcg_v2.rate_buckets VALUES(%s,10,now()-interval '1 minute')",
            (key,),
        )
    transaction = repo.transaction
    cleaned = []

    class Interleaved:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, params=None):
            result = self.db.execute(sql, params)
            if (
                sql.startswith("SELECT count,reset_at")
                and params == (key,)
                and not cleaned
            ):
                cleaned.append(True)
                repo.maintain()
            return result

    @contextmanager
    def interleaved():
        with transaction() as db:
            yield Interleaved(db)

    monkeypatch.setattr(repo, "transaction", interleaved)
    repo.rate("peer")
    assert cleaned == [True]
    with repo.db() as db:
        assert db.execute(
            "SELECT count FROM ptcg_v2.rate_buckets WHERE key=%s", (key,)
        ).fetchone() == {"count": 1}
    with pytest.raises(ServiceError) as error:
        repo.rate("peer")
    assert error.value.status == 429
