# ruff: noqa: F811
"""Closing a page must not force a native restart or reusable secret file."""

import io
import re
from types import SimpleNamespace

from ptcg_desktop_api.launch import console_windows, open_window
from tests.desktop_host.test_host import local, launch, session  # noqa: F401


def test_same_browser_can_reopen_after_window_close(local):
    host, web = local
    launch(host, web)
    first = session(web).json()["capability"]
    host.owner.revoke_window(first)
    assert web.get("/").status_code == 200
    second = session(web).json()["capability"]
    assert second != first and second in host.owner.windows
    assert len(host.sessions) == 1
    assert host.owner.profiles["test"].client is None


def test_terminal_reissues_after_expiry_without_restarting_host(local, capsys):
    host, web = local
    initial = open_window(host, no_browser=True)
    old = re.search('name="ticket" value="([^"]+)"', initial.read_text())[1]
    host.tickets[old] = (0, initial)
    console_windows(
        host,
        SimpleNamespace(should_exit=False),
        no_browser=True,
        stream=io.StringIO("r\n"),
    )
    assert not initial.exists() and old not in host.tickets
    assert len(host.tickets) == 1
    new, (_, path) = next(iter(host.tickets.items()))
    output = capsys.readouterr().out
    assert str(path) in output and host.origin + "/" in output
    assert old not in output and new not in output
    assert not host.sessions and host.owner.profiles["test"].client is None
    assert (
        web.post(
            "/launch", data={"ticket": old}, headers={"Origin": "null"}
        ).status_code
        == 403
    )
    assert (
        web.post(
            "/launch", data={"ticket": new}, headers={"Origin": "null"}
        ).status_code
        == 200
    )
    assert not path.exists()
    assert (
        web.post(
            "/launch", data={"ticket": new}, headers={"Origin": "null"}
        ).status_code
        == 403
    )
    assert session(web).status_code == 200


def test_console_unknown_input_and_shutdown_do_not_issue_tickets(local):
    host, _ = local
    console_windows(
        host,
        SimpleNamespace(should_exit=False),
        no_browser=True,
        stream=io.StringIO("help\n\n"),
    )
    console_windows(
        host,
        SimpleNamespace(should_exit=True),
        no_browser=True,
        stream=io.StringIO("r\n"),
    )
    assert not host.tickets


def test_console_launch_limit_preserves_backend(local, capsys):
    host, web = local
    console_windows(
        host,
        SimpleNamespace(should_exit=False),
        no_browser=True,
        stream=io.StringIO("r\n" * 9),
    )
    assert len(host.tickets) == 8
    assert "LAUNCH_LIMIT" in capsys.readouterr().out
    assert web.get("/").status_code == 200
    assert not host.sessions
