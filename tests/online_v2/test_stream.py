"""Slow/disappearing peers must not hold a stream slot indefinitely."""

import asyncio
from types import SimpleNamespace
import pytest
from ptcg_online_server.stream import Streams


def test_slow_sender_is_cancelled_at_deadline():
    cancelled = []

    class SlowPeer:
        async def send_json(self, value):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

    async def run():
        with pytest.raises(TimeoutError):
            await Streams(None, None).send(SlowPeer(), {})

    asyncio.run(run())
    assert cancelled == [True]


def test_disappearing_rejected_peer_releases_capacity():
    class GonePeer:
        scope = {"headers": []}

        async def close(self, code):
            raise RuntimeError("already disconnected")

    service = Streams(SimpleNamespace(max_streams=1), None)
    asyncio.run(service.serve(GonePeer()))
    assert service.active == 0
