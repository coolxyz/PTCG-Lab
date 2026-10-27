"""Native pipe authorization/lifecycle regressions, independent of LAN servers."""

import io
import json
import select
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx
import pytest
from ptcg_desktop_api.native import LIMIT, StateLock, frame

NONCE = "a" * 64


@pytest.fixture
def tmp_path(tmp_path_factory):
    # macOS exposes its temporary root through /var -> /private/var. The native
    # profile contract deliberately refuses any symlink in a supplied path.
    return tmp_path_factory.mktemp("native-profile").resolve()


def start_value(state):
    return {
        "schema": 1,
        "type": "start",
        "nonce": NONCE,
        "stateDir": str(state),
        "config": {"servers": {}},
    }


def read(child):
    assert select.select([child.stdout], [], [], 10)[0], "native child did not answer"
    return json.loads(child.stdout.readline())


@pytest.fixture
def backend(tmp_path):
    build = tmp_path / "gui"
    build.mkdir()
    (build / "online.html").write_text("<h1>Synthetic GUI</h1>")
    children = []

    def launch(state=None, value=None):
        # Installed wheel outside the checkout, as used by the bundled runtime.
        child = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "from ptcg_desktop_api.native import main; import sys; raise SystemExit(main(build=sys.argv[1]))",
                str(build),
            ],
            cwd=tmp_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        children.append(child)
        child.stdin.write(
            json.dumps(
                value
                if value is not None
                else start_value(state or tmp_path / "profile")
            )
            + "\n"
        )
        child.stdin.flush()
        return child, read(child)

    yield launch
    for child in children:
        if child.stdin and not child.stdin.closed:
            child.stdin.close()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        child.stdout.close()
        child.stderr.close()


def test_native_cookie_auth_is_same_origin_and_not_http_mintable(backend):
    child, ready = backend()
    assert ready["pid"] == child.pid and ready["nonce"] == NONCE
    assert ready["type"] == "ready" and len(ready["cookie"]) == 43
    origin = ready["origin"]
    body = {"windowId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
    with httpx.Client(base_url=origin, trust_env=False) as web:
        assert (
            web.post("/session", json=body, headers={"Origin": origin}).status_code
            == 403
        )
        web.cookies.set("ptcg-local", ready["cookie"], domain="127.0.0.1", path="/")
        assert (
            web.post(
                "/session", json=body, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        result = web.post("/session", json=body, headers={"Origin": origin})
        assert result.status_code == 200 and "capability" in result.json()
        assert ready["cookie"] not in result.text
        assert web.get("/native_session").status_code == 404
        assert (
            web.post(
                "/authorize", json={"code": ready["cookie"]}, headers={"Origin": origin}
            ).status_code
            == 404
        )


@pytest.mark.parametrize("mode", ["shutdown", "eof", "invalid-command"])
def test_parent_shutdown_closes_listener_and_releases_profile(backend, tmp_path, mode):
    child, ready = backend()
    if mode == "eof":
        child.stdin.close()
    else:
        child.stdin.write(
            json.dumps(
                {
                    "schema": 1,
                    "type": "shutdown",
                    "nonce": NONCE if mode == "shutdown" else "b" * 64,
                }
            )
            + "\n"
        )
        child.stdin.flush()
    assert child.wait(timeout=10) == 0
    port = urlsplit(ready["origin"]).port
    with socket.socket() as connection:
        assert connection.connect_ex(("127.0.0.1", port)) != 0
    assert not list((tmp_path / "profile/launch").glob("*.html"))
    lock = StateLock(tmp_path / "profile")
    lock.close()
    _, reopened = backend()
    assert reopened["cookie"] != ready["cookie"]


def test_second_backend_cannot_mutate_a_running_profile(backend, tmp_path):
    first, ready = backend()
    second, result = backend()
    assert result == {"schema": 1, "type": "error", "code": "NATIVE_START_FAILED"}
    assert second.wait(timeout=10) == 1 and first.poll() is None
    assert httpx.get(ready["origin"], trust_env=False).status_code == 200


def test_killed_backend_releases_port_and_profile_without_touching_other_owner(backend):
    first, ready = backend()
    first.kill()
    first.wait(timeout=10)
    with socket.socket() as connection:
        assert connection.connect_ex(("127.0.0.1", urlsplit(ready["origin"]).port)) != 0
    second, reopened = backend()
    assert second.poll() is None and reopened["cookie"] != ready["cookie"]


def test_hard_killed_parent_closes_inherited_pipe_and_backend_exits(tmp_path):
    build = tmp_path / "gui"
    build.mkdir()
    (build / "online.html").write_text("<h1>Synthetic GUI</h1>")
    # The intermediate process owns the only writer, just as the native shell does.
    script = """
import json, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-I', '-B', '-c',
    'from ptcg_desktop_api.native import main; import sys; raise SystemExit(main(build=sys.argv[1]))', sys.argv[1]],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
child.stdin.write(sys.argv[2] + '\\n'); child.stdin.flush()
ready = json.loads(child.stdout.readline())
print(json.dumps({'pid': child.pid, 'origin': ready['origin']}), flush=True)
time.sleep(60)
"""
    parent = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            script,
            str(build),
            json.dumps(start_value(tmp_path / "profile")),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid = None
    try:
        ready = read(parent)
        child_pid = ready["pid"]
        parent.kill()
        parent.wait(timeout=10)
        port = urlsplit(ready["origin"]).port
        for _ in range(100):
            with socket.socket() as connection:
                if connection.connect_ex(("127.0.0.1", port)) != 0:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("orphan backend still listening")
        # Listener closes before the shutdown finalizer releases the state lock.
        for _ in range(100):
            try:
                lock = StateLock(tmp_path / "profile")
                lock.close()
                child_pid = None
                break
            except BlockingIOError:
                time.sleep(0.1)
        else:
            pytest.fail("orphan backend retained profile lock")
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=10)
        parent.stdout.close()
        parent.stderr.close()
        if child_pid:
            # Only the synthetic owned child may be cleaned up after failure.
            import os
            import signal

            try:
                os.kill(child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize(
    "change",
    [
        {"schema": 2},
        {"type": "other"},
        {"nonce": "short"},
        {"stateDir": "relative"},
        {"config": {"servers": []}},
        {"config": {"servers": {}, "manifests": "bad"}},
        {"extra": "field"},
    ],
)
def test_invalid_start_fails_before_creating_state(backend, tmp_path, change):
    state = tmp_path / "uncreated"
    value = start_value(state) | change
    child, result = backend(value=value)
    assert result["code"] == "NATIVE_START_FAILED"
    assert child.wait(timeout=10) == 1 and not state.exists()
    assert not child.stderr.read()


def test_pipe_frame_is_bounded_and_requires_a_newline():
    for value in ["", "{}", "a" * (LIMIT + 1) + "\n"]:
        with pytest.raises(ValueError):
            frame(io.StringIO(value))
    assert frame(io.StringIO("{}\n")) == {}


def test_unsafe_profile_links_modes_and_lock_links_are_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        StateLock(linked / "child")
    assert not (target / "child").exists()
    target.chmod(0o755)
    with pytest.raises(ValueError):
        StateLock(target)
    target.chmod(0o700)
    file = tmp_path / "sentinel"
    file.write_text("unchanged")
    (target / "native.lock").symlink_to(file)
    with pytest.raises(OSError):
        StateLock(target)
    assert file.read_text() == "unchanged"
