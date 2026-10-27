# ruff: noqa: F811

"""P6-B durable catch-up, bounded metadata scans and isolated consumers."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from ptcg_online_server.api import create_app
from ptcg_online_server.auth import Sessions
from ptcg_online_server.config import Settings
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.matches import Matches
from ptcg_online_server.repository import Repository
from ptcg_online_server.stream import Streams
from ptcg_online_server.stream_hub import StreamHub
from tests.online_v2.test_matches import game, command  # noqa: F401


def test_stream_commit_hints_only_after_commit_and_never_after_rollback(repo):
    hints = []
    repo.stream_wakeup = lambda: hints.append(True)
    with repo.transaction() as db:
        db.stream_changed = True
        assert not hints
    assert hints == [True]
    with pytest.raises(RuntimeError):
        with repo.transaction() as db:
            db.stream_changed = True
            raise RuntimeError("rollback")
    with repo.transaction():
        pass
    assert hints == [True]
    token = Sessions(repo).create(name="test", device_name="test")["accessToken"]
    assert len(hints) == 2
    Sessions(repo).revoke(token)
    assert len(hints) == 3


def test_stream_idle_sql_is_constant_and_private_frames_are_not_loaded(game):
    service, _, tokens, mid, _ = game

    async def measure():
        samples = []
        with Repository(service.repo.settings) as pooled:
            matches = Matches(pooled, service.worker)
            views = [matches.get(t, mid) for t in tokens]
            for count in (1, 8, 32):
                hub = StreamHub(matches)
                entries = [
                    hub.add(tokens[i % 2], mid, views[i % 2]["streamCursor"])
                    for i in range(count)
                ]
                for entry in entries:
                    entry.changed.clear()
                before = pooled.metrics.snapshot()["metrics"]["db.sql"]["count"]
                for _ in range(10):
                    await hub.scan()
                central = (
                    pooled.metrics.snapshot()["metrics"]["db.sql"]["count"] - before
                )
                assert central == 30
                assert all(not e.changed.is_set() and not e.error for e in entries)
                before = pooled.metrics.snapshot()["metrics"]["db.sql"]["count"]
                for _ in range(10):
                    for i in range(count):
                        assert (
                            matches.stream(tokens[i % 2], mid, entries[i].cursor) == []
                        )
                polling = (
                    pooled.metrics.snapshot()["metrics"]["db.sql"]["count"] - before
                )
                assert polling == 90 * count
                samples.append(
                    dict(
                        subscriptions=count,
                        sweeps=10,
                        centralizedSql=central,
                        pollingSql=polling,
                        frameReads=0,
                    )
                )
                if count == 32:
                    with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
                        hub.add(tokens[0], mid, None)
                for entry in entries:
                    hub.remove(entry)
                before = pooled.metrics.snapshot()["metrics"]["db.sql"]["count"]
                await hub.scan()
                assert pooled.metrics.snapshot()["metrics"]["db.sql"]["count"] == before
            return samples

    samples = asyncio.run(measure())
    directory = os.environ.get("PTCG_V2_REPORT_DIRECTORY")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / "p6b-stream-sql.json").write_text(
            json.dumps(
                {
                    "workload": "idle-stream-metadata-v1",
                    "samples": samples,
                    "capacityMeasured": False,
                },
                indent=2,
            )
            + "\n"
        )


@pytest.mark.parametrize("change", ["access", "session", "revoke", "missing-member"])
def test_stream_batch_revalidates_expiry_revocation_and_membership(game, change):
    service, _, tokens, mid, _ = game

    async def run():
        hub = StreamHub(service)
        entries = [hub.add(t, mid, service.get(t, mid)["streamCursor"]) for t in tokens]
        for e in entries:
            e.changed.clear()
        if change == "revoke":
            Sessions(service.repo).revoke(tokens[0])
        elif change == "missing-member":
            entries[0].match_id = "unknown"
        else:
            identity = Sessions(service.repo).authenticate(tokens[0])
            with service.repo.transaction() as db:
                if change == "access":
                    db.execute(
                        "UPDATE ptcg_v2.access_tokens SET expires_at=now() WHERE session_id=%s",
                        (identity["sessionId"],),
                    )
                else:
                    db.execute(
                        "UPDATE ptcg_v2.sessions SET expires_at=now() WHERE id=%s",
                        (identity["sessionId"],),
                    )
        await hub.scan()
        assert entries[0].error.code == (
            "FORBIDDEN" if change == "missing-member" else "UNAUTHORIZED"
        )
        assert entries[0].changed.is_set()
        assert entries[1].error is None and not entries[1].changed.is_set()

    asyncio.run(run())


def test_stream_missing_commit_hint_catches_up_and_rejects_foreign_cursor(game):
    service, _, tokens, mid, controllers = game
    app = create_app(service.repo.settings, worker=service.worker, background=False)
    with TestClient(app, base_url=service.repo.settings.public_origin) as client:
        # Separate writer repository has no local callback; durable sweep must work.
        assert service.repo.stream_wakeup is None
        headers = {
            "Authorization": "Bearer " + tokens[0],
            "Host": service.repo.settings.host,
        }
        with client.websocket_connect("/api/online/v2/stream", headers=headers) as ws:
            ws.send_json(dict(protocolVersion="2.0", matchId=mid, streamCursor=None))
            first = ws.receive_json()["snapshot"]
            started = time.monotonic()
            service.command(tokens[0], mid, command(first), controllers[0])
            pushed = ws.receive_json()
            assert pushed["snapshot"]["matchSeq"] == 1
            assert time.monotonic() - started < 3
            assert "private-PLAYER2" not in json.dumps(pushed)
        foreign = service.get(tokens[1], mid)["streamCursor"]
        with client.websocket_connect("/api/online/v2/stream", headers=headers) as ws:
            ws.send_json(dict(protocolVersion="2.0", matchId=mid, streamCursor=foreign))
            assert ws.receive_json()["code"] == "RESYNC_REQUIRED"
    assert not app.state.streams.hub.entries and app.state.streams.hub.task.done()
    assert app.state.repository.stream_wakeup is None


def test_stream_failed_scan_fails_closed_and_slow_peer_does_not_block(
    game, monkeypatch
):
    service, _, tokens, mid, controllers = game

    class Peer:
        def __init__(self, slow):
            self.slow, self.sent = slow, asyncio.Queue()

        async def receive(self):
            await asyncio.Event().wait()

        async def send_json(self, value):
            if self.slow:
                await asyncio.sleep(10)
            await self.sent.put(value)

    async def run():
        streams = Streams(service.repo.settings, service)
        streams.hub.start()
        slow, fast = Peer(True), Peer(False)
        tasks = [
            asyncio.create_task(
                streams.centralized(p, t, dict(matchId=mid, streamCursor=None))
            )
            for p, t in zip((slow, fast), tokens)
        ]
        try:
            initial = await asyncio.wait_for(fast.sent.get(), 2)
            assert initial["snapshot"]["viewerSeat"] == "PLAYER2"
            await asyncio.to_thread(
                service.command,
                tokens[0],
                mid,
                command(service.get(tokens[0], mid)),
                controllers[0],
            )
            pushed = await asyncio.wait_for(fast.sent.get(), 2)
            assert pushed["snapshot"]["matchSeq"] == 1
            assert not tasks[0].done()

            def unavailable(_):
                raise ServiceError("INTERNAL_ERROR", 503, retryable=True)

            monkeypatch.setattr(service, "stream_heads", unavailable)
            await streams.hub.scan()
            with pytest.raises(ServiceError, match="INTERNAL_ERROR"):
                await asyncio.wait_for(tasks[1], 1)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await streams.hub.stop()
        assert not streams.hub.entries

    asyncio.run(run())


def test_stream_rollback_switch_keeps_snapshot_and_revoke(game):
    service, _, tokens, mid, _ = game
    settings = replace(service.repo.settings, stream_hub_enabled=False)
    app = create_app(settings, worker=service.worker, background=False)
    with TestClient(app, base_url=settings.public_origin) as client:
        assert app.state.streams.hub.task is None
        with client.websocket_connect(
            "/api/online/v2/stream",
            headers={"Authorization": "Bearer " + tokens[0], "Host": settings.host},
        ) as ws:
            ws.send_json(dict(protocolVersion="2.0", matchId=mid, streamCursor=None))
            assert ws.receive_json()["type"] == "snapshot"
            Sessions(service.repo).revoke(tokens[0])
            assert ws.receive_json()["code"] == "UNAUTHORIZED"


def test_stream_switch_rejects_ambiguous_values(monkeypatch):
    with pytest.raises(ValueError, match="INVALID_STREAM_CONFIG"):
        Settings(
            "postgresql://unused", "test", "https://example.test", stream_hub_enabled=1
        )
    monkeypatch.delenv("PTCG_ONLINE_V2_DATABASE_URL_FILE", raising=False)
    for key, value in dict(
        DATABASE_URL="postgresql://unused",
        SERVER_ID="test",
        PUBLIC_ORIGIN="https://example.test",
        STREAM_HUB_ENABLED="0",
    ).items():
        monkeypatch.setenv("PTCG_ONLINE_V2_" + key, value)
    assert Settings.from_env().stream_hub_enabled is False
    monkeypatch.setenv("PTCG_ONLINE_V2_STREAM_HUB_ENABLED", "maybe")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_stream_ping_flood_keeps_previous_read_budget(game):
    service, _, tokens, mid, _ = game

    class Peer:
        def __init__(self):
            self.reads = 0
            self.sent = asyncio.Event()

        async def receive(self):
            self.reads += 1
            return {
                "type": "websocket.receive",
                "text": '{"protocolVersion":"2.0","type":"ping"}',
            }

        async def send_json(self, value):
            self.sent.set()

    async def run():
        peer = Peer()
        streams = Streams(service.repo.settings, service)
        task = asyncio.create_task(
            streams.centralized(peer, tokens[0], dict(matchId=mid, streamCursor=None))
        )
        try:
            await asyncio.wait_for(peer.sent.wait(), 2)
            await asyncio.sleep(0.1)
            assert peer.reads == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not streams.hub.entries

    asyncio.run(run())
