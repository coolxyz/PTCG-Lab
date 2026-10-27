"""Order the initial TLS write before the synchronous receive thread starts."""

from threading import Event
from websockets.sync.client import ClientConnection


class OrderedHandshakeConnection(ClientConnection):
    """Avoid overlapping TLS reads with the first HTTP upgrade write.

    websockets 15 starts receiving in its constructor, before handshake() sends
    the request. On our supported macOS TLS runtime this can stall before the
    request reaches the server. Only the first write is ordered; frame handling,
    certificate checks, deadlines and the normal duplex connection are unchanged.
    """

    def __init__(self, *args, **kwargs):
        self._upgrade_sent = Event()
        super().__init__(*args, **kwargs)

    def recv_events(self):
        self._upgrade_sent.wait()
        super().recv_events()

    def send_data(self):
        try:
            return super().send_data()
        finally:
            self._upgrade_sent.set()

    def close_socket(self):
        # connect() closes on invalid headers or failed writes before joining
        # the receiver; do not leave that thread waiting for an unsent request.
        self._upgrade_sent.set()
        return super().close_socket()
