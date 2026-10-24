"""Transport-level tests do not need a DB or real rule assets."""

import asyncio
from dataclasses import replace
import pytest
from ptcg_online_server.api import Boundary
from ptcg_online_server.config import Settings
from ptcg_online_server.contracts import decode
from ptcg_online_server.errors import ServiceError


def config():
    return Settings(
        "postgresql://synthetic.invalid/db",
        "test",
        "http://127.0.0.1:8784",
        allow_loopback_http=True,
    )


class NoDatabase:
    def rate(self, *args, **kwargs):
        pass


async def invoke(chunks, *, headers=(), settings=None):
    sent, called = [], []
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/online/v2/sessions",
        "query_string": b"",
        "headers": [
            (b"host", b"127.0.0.1:8784"),
            (b"content-type", b"application/json"),
            *headers,
        ],
    }
    stream = iter(chunks)

    async def receive():
        return next(stream)

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        called.append(await receive())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    await Boundary(app, settings or config(), NoDatabase())(scope, receive, send)
    return sent, called


def test_chunked_body_bound_ignores_forged_small_content_length():
    sent, called = asyncio.run(
        invoke(
            [
                {"type": "http.request", "body": b"x" * 10000, "more_body": True},
                {"type": "http.request", "body": b"x" * 10000, "more_body": False},
            ],
            headers=[(b"content-length", b"1")],
        )
    )
    assert sent[0]["status"] == 413 and not called


def test_duplicate_auth_is_rejected_and_disconnect_has_no_effect():
    sent, called = asyncio.run(
        invoke(
            [], headers=[(b"authorization", b"first"), (b"authorization", b"second")]
        )
    )
    assert sent[0]["status"] == 400 and not called
    sent, called = asyncio.run(invoke([{"type": "http.disconnect"}]))
    assert not sent and not called


def test_inflight_admission_rejects_excess_and_slow_body_times_out():
    async def run():
        sent = []

        async def send(message):
            sent.append(message)

        entered = asyncio.Event()

        async def slow():
            entered.set()
            await asyncio.Event().wait()

        async def app(*args):
            raise AssertionError("A timed-out request must not reach the app")

        boundary = Boundary(
            app, replace(config(), max_inflight=1, body_timeout=0.05), NoDatabase()
        )
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/online/v2/sessions",
            "headers": [
                (b"host", b"127.0.0.1:8784"),
                (b"content-type", b"application/json"),
            ],
        }
        one = asyncio.create_task(boundary(scope, slow, send))
        await entered.wait()
        await boundary(scope, slow, send)
        await one
        assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [
            503,
            408,
        ]
        assert boundary.inflight == 0

    asyncio.run(run())


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?token=secret",
    ],
)
def test_insecure_or_ambiguous_deployment_origin_rejected(origin):
    with pytest.raises(ValueError):
        replace(config(), public_origin=origin)


def test_session_names_and_duplicate_json_keys_have_strict_contracts():
    assert decode("Create", b'{"protocolVersion":"2.0","name":"x"}')["name"] == "x"
    for name in ("x\n", "\x00", "x\x7f"):
        import json

        with pytest.raises(ServiceError):
            decode(
                "Create", json.dumps({"protocolVersion": "2.0", "name": name}).encode()
            )
    with pytest.raises(ServiceError):
        decode("Create", b'{"protocolVersion":"2.0","name":"a","name":"b"}')
