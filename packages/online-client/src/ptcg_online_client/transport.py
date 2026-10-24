"""Fixed-origin native transports. No redirects, cookies or environment proxies."""

from dataclasses import dataclass, field
from hashlib import sha256
import http.client
import json
from pathlib import Path
import re
import ssl
from urllib.parse import urlsplit
from websockets.sync.client import connect
from .contracts import ClientError, unpack, checked

PREFIX = "/api/online/v2"
MAX_RESPONSE = 2 * 1024 * 1024


@dataclass(frozen=True)
class Server:
    origin: str
    ca_file: str | None = field(default=None, repr=False)
    allow_loopback_http: bool = False
    _ca: str | None = field(default=None, init=False, repr=False)
    _key: str = field(default="", init=False, repr=False)

    def __post_init__(self):
        try:
            value = urlsplit(self.origin)
            port = value.port
            if (
                value.scheme not in {"https", "http"}
                or not value.hostname
                or value.username
                or value.password
                or value.query
                or value.fragment
                or value.path
                or not self.origin.isascii()
                or self.origin != self.origin.lower()
                or re.search(r"[\s\\]", self.origin)
                or self.origin != value.scheme + "://" + value.netloc
                or port == (443 if value.scheme == "https" else 80)
            ):
                raise ValueError()
            if value.scheme == "http" and not (
                self.allow_loopback_http
                and value.hostname in {"127.0.0.1", "localhost", "::1"}
            ):
                raise ValueError()
        except ValueError:
            raise ClientError("INVALID_SERVER") from None
        try:
            ca = Path(self.ca_file).read_text() if self.ca_file else None
            trust = sha256(ca.encode()).hexdigest() if ca else "system"
            object.__setattr__(self, "_ca", ca)
            object.__setattr__(
                self, "_key", sha256((self.origin + "\n" + trust).encode()).hexdigest()
            )
        except (OSError, UnicodeError):
            raise ClientError("INVALID_TRUST_ROOT") from None

    @property
    def key(self):
        # A different trust root is a different profile even at the same URL.
        return self._key

    def context(self):
        return ssl.create_default_context(cadata=self._ca)


class Transport:
    def __init__(self, server):
        self.server = server
        self.context = server.context()

    def request(self, method, path, body=None, token=None, controller=None):
        # Only library-owned paths reach here; never accept an absolute URL.
        if not re.fullmatch(r"/[A-Za-z0-9/_-]+", path):
            raise ClientError("INVALID_ROUTE")
        raw = (
            json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
            if body is not None
            else None
        )
        if raw is not None and len(raw) > 16384:
            raise ClientError("PAYLOAD_TOO_LARGE")
        target = urlsplit(self.server.origin)
        connection = (
            http.client.HTTPSConnection(
                target.hostname, target.port, timeout=10, context=self.context
            )
            if target.scheme == "https"
            else http.client.HTTPConnection(target.hostname, target.port, timeout=10)
        )
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if raw is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        if controller:
            headers["X-PTCG-Controller"] = controller
        try:
            connection.request(method, PREFIX + path, body=raw, headers=headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise ClientError("REDIRECT_REFUSED", response.status)
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise ClientError("INVALID_RESPONSE")
            if (
                response.getheader("Content-Type", "").split(";")[0].strip()
                != "application/json"
            ):
                raise ClientError("INVALID_RESPONSE")
            if int(response.getheader("Content-Length", "0")) > MAX_RESPONSE:
                raise ClientError("RESPONSE_TOO_LARGE")
            data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE:
                raise ClientError("RESPONSE_TOO_LARGE")
            value = unpack(data)
            if not 200 <= response.status < 300:
                error = checked("ProtocolError", value)
                raise ClientError(
                    error["code"], response.status, retryable=error["retryable"]
                )
            return value
        except ssl.SSLError:
            raise ClientError("TLS_FAILED") from None
        except (OSError, http.client.HTTPException):
            raise ClientError("NETWORK_UNAVAILABLE", retryable=True) from None
        except (ValueError, UnicodeError):
            raise ClientError("INVALID_RESPONSE") from None
        finally:
            connection.close()

    def stream(self, token):
        uri = (
            self.server.origin.replace("https:", "wss:").replace("http:", "ws:")
            + PREFIX
            + "/stream"
        )
        return connect(
            uri,
            ssl=self.context if uri.startswith("wss:") else None,
            proxy=None,
            compression=None,
            additional_headers={"Authorization": "Bearer " + token},
            open_timeout=15,
            close_timeout=2,
            max_size=MAX_RESPONSE,
            max_queue=8,
        )
