"""Test-only process factory. Faults are private filesystem markers, never HTTP APIs."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import uvicorn
from ptcg_desktop_api.bridge import Bridge
from ptcg_desktop_api.host import Host, create_host_app
from ptcg_desktop_api.resources import Resources
from ptcg_online_client.contracts import ClientError
from ptcg_online_client.transport import Server


def main():
    config = json.loads(Path(os.environ["PTCG_TEST_HOST_CONFIG"]).read_text())
    root = Path(config["state"])
    owner = Bridge({"lan": Server(**config["server"])}, root / "credentials")
    profile = owner.profiles["lan"]
    client = profile.client = owner.factory(profile.server, owner.storage)
    original = client.transport

    class FaultTransport:
        def request(self, method, path, *args):
            result = original.request(method, path, *args)
            marker = root / "drop-command"
            if path.endswith("/commands") and marker.exists():
                marker.unlink()
                (root / "dropped-command").touch()
                raise ClientError("NETWORK_UNAVAILABLE", retryable=True)
            return result

        @contextmanager
        def stream(self, token):
            with original.stream(token) as ws:

                class Socket:
                    def send(self, value):
                        ws.send(value)

                    def recv(self, **kw):
                        marker = root / "drop-stream"
                        if marker.exists():
                            marker.unlink()
                            (root / "dropped-stream").touch()
                            ws.close()
                            raise ClientError("STREAM_TIMEOUT", retryable=True)
                        return ws.recv(**kw)

                yield Socket()

    client.transport = FaultTransport()
    resources = Resources(root / "resources", config["manifests"])
    host = Host(owner, config["origin"], config["build"], resources, root / "launch")
    ticket = host.launch()
    ready = root / "ready.json"
    ready.write_text(json.dumps({"launch": str(ticket), "origin": host.origin}))
    ready.chmod(0o600)
    uvicorn.run(
        create_host_app(host),
        host="127.0.0.1",
        port=config["port"],
        access_log=False,
        proxy_headers=False,
        log_level="critical",
        ws="none",
    )


if __name__ == "__main__":
    main()
