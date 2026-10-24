"""Real HTTP and abrupt process restart; only fresh test DBs and free ports."""

from contextlib import contextmanager
import json
import os
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import URLError


@contextmanager
def server(settings, tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with (tmp_path / f"server-{port}.log").open("w") as log:
        child = subprocess.Popen(
            [sys.executable, "-m", "ptcg_online_server"],
            env={
                **os.environ,
                "PTCG_ONLINE_V2_DATABASE_URL": settings.database_url,
                "PTCG_ONLINE_V2_SERVER_ID": settings.server_id,
                "PTCG_ONLINE_V2_PUBLIC_ORIGIN": origin,
                "PTCG_ONLINE_V2_PORT": str(port),
                "PTCG_ONLINE_V2_ALLOW_LOOPBACK_HTTP": "1",
                **(
                    {
                        "PTCG_ONLINE_V2_RULE_DIRECTORY": settings.rule_directory,
                        "PTCG_ONLINE_V2_RULE_MANIFEST_SHA256": settings.rule_manifest_sha256,
                        "PTCG_ONLINE_V2_APPROVED_RULE_RELEASE_ID": settings.approved_rule_release_id,
                    }
                    if settings.rule_directory
                    else {}
                ),
            },
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(60):
                assert child.poll() is None, "Dedicated API process failed startup"
                try:
                    with urlopen(origin + "/healthz", timeout=0.5) as result:
                        if result.status == 200:
                            break
                except URLError:
                    time.sleep(0.1)
            else:
                raise AssertionError("Dedicated API did not become alive")
            yield origin
        finally:
            # Simulates power loss; no cooperative shutdown or in-memory token cache.
            child.kill()
            child.wait(timeout=5)


def call(origin, path, *, body=None, access=None):
    headers = {"Content-Type": "application/json"}
    if access:
        headers["Authorization"] = "Bearer " + access
    request = Request(
        origin + "/api/online/v2" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
    )
    with urlopen(request, timeout=5) as result:
        return json.loads(result.read())


def test_tokens_survive_abrupt_api_restart_over_http(repo, tmp_path):
    with server(repo.settings, tmp_path) as origin:
        tokens = call(
            origin, "/sessions", body={"protocolVersion": "2.0", "name": "重启验收"}
        )
        assert (
            call(origin, "/sessions/current", access=tokens["accessToken"])["sessionId"]
            == tokens["sessionId"]
        )
    with server(repo.settings, tmp_path) as origin:
        assert (
            call(origin, "/sessions/current", access=tokens["accessToken"])["userId"]
            == tokens["userId"]
        )
        rotated = call(
            origin,
            "/sessions/refresh",
            body={"protocolVersion": "2.0", "refreshToken": tokens["refreshToken"]},
        )
        assert rotated["sessionId"] == tokens["sessionId"]
        assert call(
            origin,
            "/sessions/revoke",
            body={"protocolVersion": "2.0"},
            access=rotated["accessToken"],
        )["revoked"]
    with server(repo.settings, tmp_path) as origin:
        from urllib.error import HTTPError
        import pytest

        with pytest.raises(HTTPError) as error:
            call(origin, "/sessions/current", access=rotated["accessToken"])
        assert error.value.code == 401
