"""The integrated host must keep product and native auth on one origin."""

from contextlib import asynccontextmanager
import re
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from ptcg_desktop_api.bridge import Bridge
from ptcg_desktop_api.host import Host
from ptcg_desktop_api.product import create_product_app
from ptcg_desktop_api.resources import Resources
from ptcg_online_client.transport import Server

ORIGIN = "http://127.0.0.1:18789"


def make(tmp_path, *, fail=False):
    events = []

    @asynccontextmanager
    async def lifespan(app):
        events.append("start")
        if fail:
            raise RuntimeError("startup failure")
        try:
            yield
        finally:
            events.append("stop")

    async def page(request):
        return HTMLResponse("product shell")

    async def decks(request):
        events.append("decks")
        return JSONResponse({"query": request.query_params.get("q"), "ok": True})

    local = Starlette(
        routes=[Route("/", page), Route("/api/decks", decks, methods=["GET", "POST"])],
        lifespan=lifespan,
    )
    build = tmp_path / "web"
    (build / "assets").mkdir(parents=True)
    (build / "online.html").write_text("online shell")
    (build / "assets/online-test.js").write_text("const online=true;")
    owner = Bridge({"lan": Server("https://example.test")}, tmp_path / "credentials")
    online = Host(
        owner, ORIGIN, build, Resources(tmp_path / "resources"), tmp_path / "launch"
    )
    return create_product_app(local, online), online, events


def web(app):
    return TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 1234))


def test_one_app_product_online_assets_and_same_origin_session(tmp_path):
    app, online, events = make(tmp_path)
    with web(app) as browser:
        assert browser.get("/").text == "product shell"
        assert browser.get("/online").text == "online shell"
        assert browser.get("/assets/online-test.js").status_code == 200
        assert browser.get("/api/decks?q=existing").json()["query"] == "existing"
        assert browser.post("/api/decks", headers={"Origin": ORIGIN}).status_code == 200
        body = {"windowId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
        assert (
            browser.post("/session", json=body, headers={"Origin": ORIGIN}).status_code
            == 403
        )
        path = online.launch()
        ticket = re.search('name="ticket" value="([^"]+)"', path.read_text())[1]
        assert (
            browser.post(
                "/launch", data={"ticket": ticket}, headers={"Origin": "null"}
            ).text
            == "product shell"
        )
        assert not path.exists()
        capability = browser.post(
            "/session", json=body, headers={"Origin": ORIGIN}
        ).json()["capability"]
        browser.cookies.clear()
        result = browser.post(
            "/api/desktop/online/v1/profiles",
            json={},
            headers={"Origin": ORIGIN, "X-PTCG-Bridge": capability},
        )
        assert result.status_code == 200 and result.json()["result"][0]["id"] == "lan"
        assert online.owner.profiles["lan"].client is None
    assert events == ["start", "decks", "decks", "stop"]
    assert not online.sessions and not online.owner.windows


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "null"},
        {"Origin": "https://evil.example"},
        {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
        {"Origin": ORIGIN, "Host": "localhost:18789"},
        {"Origin": ORIGIN, "X-Forwarded-Host": "127.0.0.1:18789"},
    ],
)
def test_permissive_legacy_write_cannot_bypass_outer_guard(tmp_path, headers):
    app, online, events = make(tmp_path)
    with web(app) as browser:
        assert browser.post("/api/decks", headers=headers).status_code == 403
        assert "decks" not in events
        assert not online.sessions


def test_cross_site_reads_and_remote_peer_rejected(tmp_path):
    app, _, events = make(tmp_path)
    with web(app) as browser:
        assert (
            browser.get(
                "/api/decks", headers={"Sec-Fetch-Site": "cross-site"}
            ).status_code
            == 403
        )
        assert (
            browser.post(
                "/api/decks", headers=[("Origin", ORIGIN), ("Origin", ORIGIN)]
            ).status_code
            == 403
        )
    with TestClient(app, base_url=ORIGIN, client=("10.0.0.2", 1234)) as browser:
        assert browser.get("/api/decks").status_code == 403
    assert "decks" not in events


def test_failed_product_startup_closes_native_host(tmp_path):
    app, online, events = make(tmp_path, fail=True)
    path = online.launch()
    with pytest.raises(RuntimeError, match="startup failure"):
        with web(app):
            pass
    assert events == ["start"]
    assert not path.exists() and not online.tickets


def test_conflicting_static_assets_refused_before_port_bind(tmp_path, monkeypatch):
    import json
    import sys
    from ptcg_desktop_api.launch import main

    _, online, _ = make(tmp_path)
    local = tmp_path / "local-build"
    (local / "assets").mkdir(parents=True)
    (local / "assets/online-test.js").write_text("different bundle")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"servers": {}, "manifests": []}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "product",
            "--config",
            str(config),
            "--state-dir",
            str(tmp_path / "new-state"),
            "--build",
            str(online.build),
        ],
    )
    called = []
    with pytest.raises(SystemExit) as error:
        main(local_factory=lambda: called.append(True), local_build=local)
    assert error.value.code == 2 and not called


def test_product_code_is_one_use_and_not_mintable_over_http(tmp_path):
    app, host, _ = make(tmp_path)
    with web(app) as browser:
        code = host.activation_code()
        assert browser.post("/authorize", json={"code": code}).status_code == 403
        assert (
            browser.post(
                "/authorize", json={"code": code}, headers={"Origin": "null"}
            ).status_code
            == 403
        )
        response = browser.post(
            "/authorize", json={"code": code}, headers={"Origin": ORIGIN}
        )
        assert response.status_code == 200
        assert (
            "HttpOnly" in response.headers["set-cookie"]
            and "SameSite=strict" in response.headers["set-cookie"]
        )
        assert (
            browser.post(
                "/authorize", json={"code": code}, headers={"Origin": ORIGIN}
            ).status_code
            == 403
        )
        assert (
            browser.post(
                "/session",
                json={"windowId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
                headers={"Origin": ORIGIN},
            ).status_code
            == 200
        )
        assert browser.get("/authorize").status_code == 404
        expired = host.activation_code()
        host.codes[expired] = 0
        assert (
            browser.post(
                "/authorize", json={"code": expired}, headers={"Origin": ORIGIN}
            ).status_code
            == 403
        )


def test_native_reopen_private_socket_and_config_binding(tmp_path):
    from ptcg_desktop_api.reopen import ReopenServer, reopen

    # AF_UNIX has a short platform path limit.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ptcg-reopen-", dir="/tmp") as directory:
        app, host, _ = make(tmp_path)
        with web(app):
            control = ReopenServer(directory, host, "config-digest")
            try:
                with pytest.raises(ValueError, match="CONFIG_MISMATCH"):
                    reopen(directory, "wrong")
                result = reopen(directory, "config-digest")
                assert result["origin"] == ORIGIN and result["code"] in host.codes
                assert not host.sessions and not host.tickets
            finally:
                control.close()
            assert not control.path.exists()


def test_local_deck_loading_does_not_contend_with_remote_owner(tmp_path):
    from types import SimpleNamespace

    app, host, _ = make(tmp_path)
    with web(app):
        owner = host.owner
        token = owner.issue_window()
        profile = owner.profiles["lan"]
        owner.product = SimpleNamespace(call=lambda p, action, body: {"decks": []})
        with profile.lock:
            assert owner.call(token, "lan", "product-decks", {}) == {"decks": []}
        assert profile.client is None


def test_product_owner_lock_prevents_second_instance_and_recovers_stale_socket(
    tmp_path,
):
    from ptcg_desktop_api.reopen import ReopenServer
    import tempfile
    import socket

    with tempfile.TemporaryDirectory(prefix="ptcg-owner-", dir="/tmp") as directory:
        app, host, _ = make(tmp_path)
        with web(app):
            first = ReopenServer(directory, host, "digest")
            try:
                with pytest.raises(BlockingIOError):
                    ReopenServer(directory, host, "digest")
            finally:
                first.close()
            # Simulate the filesystem socket left after a terminated process.
            with socket.socket(socket.AF_UNIX) as stale:
                stale.bind(str(first.path))
            second = ReopenServer(directory, host, "digest")
            second.close()
            assert not second.path.exists()
