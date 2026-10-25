import re
import time
import pytest
from starlette.testclient import TestClient
from ptcg_desktop_api.bridge import Bridge
from ptcg_desktop_api.host import Host, create_host_app
from ptcg_desktop_api.resources import Resources
from ptcg_online_client.transport import Server

ORIGIN = "http://127.0.0.1:18786"


@pytest.fixture
def local(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "online.html").write_text("<!doctype html><h1>Network</h1>")
    owner = Bridge({"test": Server("https://example.test")}, tmp_path / "private")
    host = Host(
        owner, ORIGIN, build, Resources(tmp_path / "cache"), tmp_path / "launch"
    )
    with TestClient(
        create_host_app(host), base_url=ORIGIN, client=("127.0.0.1", 1)
    ) as web:
        yield host, web


def ticket(host):
    path = host.launch()
    content = path.read_text()
    value = re.search('name="ticket" value="([^"]+)"', content)[1]
    assert value not in path.name and value not in host.origin
    assert path.stat().st_mode & 0o777 == 0o600
    return path, value


def launch(host, web):
    path, value = ticket(host)
    result = web.post(
        "/launch",
        data={"ticket": value},
        headers={"Origin": "null"},
        follow_redirects=False,
    )
    assert result.status_code == 303 and result.headers["location"] == "/"
    assert not path.exists()
    assert value not in result.text
    assert (
        "HttpOnly" in result.headers["set-cookie"]
        and "SameSite=strict" in result.headers["set-cookie"]
    )
    return value


def session(
    web, window="a" * 8 + "-" + "b" * 4 + "-" + "c" * 4 + "-" + "d" * 4 + "-" + "e" * 12
):
    return web.post("/session", json={"windowId": window}, headers={"Origin": ORIGIN})


def test_one_time_launch_cookie_and_reload_rotate_local_capability(local):
    host, web = local
    value = launch(host, web)
    assert (
        web.post(
            "/launch", data={"ticket": value}, headers={"Origin": "null"}
        ).status_code
        == 403
    )
    first = session(web).json()["capability"]
    second = session(web).json()["capability"]
    assert (
        first != second
        and first not in host.owner.windows
        and second in host.owner.windows
    )
    assert (
        session(
            web,
            "b" * 8 + "-" + "b" * 4 + "-" + "c" * 4 + "-" + "d" * 4 + "-" + "e" * 12,
        ).status_code
        == 200
    )
    assert len(host.owner.windows) == 2
    assert "capability" not in web.get("/").text
    assert (
        web.get("/").headers["content-security-policy"].find("frame-ancestors 'none'")
        >= 0
    )


def test_session_limit_removes_consumed_launch_file(local):
    host, web = local
    for _ in range(8):
        launch(host, web)
    path, value = ticket(host)
    result = web.post("/launch", data={"ticket": value}, headers={"Origin": "null"})
    assert result.status_code == 503
    assert value not in host.tickets and not path.exists()
    assert len(host.sessions) == 8


@pytest.mark.parametrize("origin", ["https://evil.example", "", ORIGIN])
def test_launch_requires_file_origin_and_native_ticket(local, origin):
    host, web = local
    path, value = ticket(host)
    assert (
        web.post(
            "/launch", data={"ticket": value}, headers={"Origin": origin}
        ).status_code
        == 403
    )
    assert path.exists() and not host.sessions


def test_expired_ticket_and_unknown_secret_never_mint_session(local):
    host, web = local
    path, value = ticket(host)
    host.tickets[value] = (time.monotonic() - 1, path)
    assert (
        web.post(
            "/launch", data={"ticket": value}, headers={"Origin": "null"}
        ).status_code
        == 403
    )
    assert not path.exists() and not host.sessions
    assert (
        web.post(
            "/launch", data={"ticket": "wrong"}, headers={"Origin": "null"}
        ).status_code
        == 403
    )


def test_session_and_catalog_require_auth_no_arbitrary_file_or_remote_url(local):
    host, web = local
    assert session(web).status_code == 403
    launch(host, web)
    assert (
        web.post(
            "/session",
            json={"windowId": "a" * 36},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    token = session(web).json()["capability"]
    # Browser bridge requests omit the HttpOnly launch cookie entirely.
    web.cookies.clear()
    headers = {"Origin": ORIGIN, "X-PTCG-Bridge": token}
    assert web.post(
        "/catalog", json={"catalogVersion": "c", "assetVersion": "a"}, headers=headers
    ).json()["missing"]
    assert (
        web.post(
            "/catalog", json={"path": "/private/secret"}, headers=headers
        ).status_code
        == 400
    )
    assert (
        web.get(
            "/resources/" + "a" * 64, headers={"Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    assert web.get("/anything.json").status_code == 404
    assert web.get("/?capability=" + token).status_code == 403
    assert web.post("/catalog", json={}, headers={"Origin": ORIGIN}).status_code == 403
    assert host.owner.profiles["test"].client is None


def test_public_shell_allows_top_level_launch_redirect_but_not_framing_or_auth(local):
    _, web = local
    headers = {
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    }
    shell = web.get("/", headers=headers)
    assert shell.status_code == 200 and "Network" in shell.text
    assert session(web).status_code == 403
    headers["Sec-Fetch-Dest"] = "iframe"
    assert web.get("/", headers=headers).status_code == 403
