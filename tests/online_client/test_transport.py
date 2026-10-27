from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ssl
from threading import Thread
import pytest
from ptcg_online_client.contracts import ClientError
from ptcg_online_client.transport import Server, Transport, MAX_RESPONSE
from tests.online_v2.test_real_matches import certificate


@contextmanager
def endpoint(body=b"{}", status=200, headers=None, tls=None):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(status)
            for key, value in (
                headers
                or {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                }
            ).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(*tls)
        http.socket = context.wrap_socket(http.socket, server_side=True)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"{'https' if tls else 'http'}://127.0.0.1:{http.server_port}", seen
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)


def test_redirect_does_not_contact_new_origin():
    with endpoint() as (target, seen):
        with endpoint(status=307, headers={"Location": target}) as (origin, _):
            transport = Transport(Server(origin, allow_loopback_http=True))
            with pytest.raises(ClientError, match="REDIRECT_REFUSED"):
                transport.request("GET", "/capabilities", token="not-to-be-forwarded")
        assert seen == []


@pytest.mark.parametrize(
    "body,headers,code",
    [
        (
            b"{}",
            {
                "Content-Type": "application/json",
                "Content-Length": str(MAX_RESPONSE + 1),
            },
            "RESPONSE_TOO_LARGE",
        ),
        (
            b"{}",
            {"Content-Type": "application/json", "Content-Encoding": "gzip"},
            "INVALID_RESPONSE",
        ),
        (
            b'{"secret":"ignored","secret":"again"}',
            {"Content-Type": "application/json"},
            "INVALID_RESPONSE",
        ),
        (b"<html>not-json</html>", {"Content-Type": "text/html"}, "INVALID_RESPONSE"),
    ],
)
def test_http_response_boundaries(body, headers, code):
    with endpoint(body=body, headers=headers) as (origin, _):
        with pytest.raises(ClientError, match=code):
            Transport(Server(origin, allow_loopback_http=True)).request(
                "GET", "/capabilities"
            )


def test_tls_rejects_untrusted_certificate_and_freezes_selected_trust(tmp_path):
    tls = certificate(tmp_path)
    with endpoint(tls=tls) as (origin, _):
        with pytest.raises(ClientError, match="TLS_FAILED"):
            Transport(Server(origin)).request("GET", "/capabilities")
        profile = Server(origin, ca_file=str(tls[0]))
        key = profile.key
        tls[0].write_text("changed-after-configuration")
        assert profile.key == key
        assert Transport(profile).request("GET", "/capabilities") == {}


def test_failed_upgrade_unblocks_receiver_before_join(monkeypatch):
    from threading import Event
    from websockets.sync.client import ClientConnection
    from ptcg_online_client.websocket import OrderedHandshakeConnection

    entered = Event()
    monkeypatch.setattr(ClientConnection, "recv_events", lambda self: entered.set())
    monkeypatch.setattr(ClientConnection, "close_socket", lambda self: None)
    connection = OrderedHandshakeConnection.__new__(OrderedHandshakeConnection)
    connection._upgrade_sent = Event()
    receiver = Thread(target=connection.recv_events, daemon=True)
    receiver.start()
    assert not entered.wait(0.02)
    # connect() calls close_socket on malformed headers, before the first send.
    connection.close_socket()
    receiver.join(timeout=1)
    assert entered.is_set() and not receiver.is_alive()
