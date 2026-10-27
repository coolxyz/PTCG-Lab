# ruff: noqa: F811
"""Exercise actual native TLS transport with two simultaneous subscribers."""

import json
import ssl
import pytest
from ptcg_online_client.transport import Server, Transport
from ptcg_online_server.repository import Repository
from tests.online_v2.test_real_lobby import real  # noqa: F401
from tests.online_v2.test_process import server
from tests.online_v2.test_real_matches import certificate
from tests.online_v2.native_pair import NativePair, pushed


def test_native_tls_repeated_dual_handshakes_keep_trust_and_private_views(
    real, tmp_path
):
    settings, inputs = real
    repo = Repository(settings)
    repo.migrate()
    tls = certificate(tmp_path)
    context = ssl.create_default_context(cafile=str(tls[0]))
    with server(settings, tmp_path, tls=tls) as origin:
        pair = NativePair(origin, context)
        pair.prepare(inputs)
        with pytest.raises(ssl.SSLCertVerificationError):
            Transport(Server(origin)).stream(pair.access[0])
        transports = [Transport(Server(origin, ca_file=str(tls[0]))) for _ in range(2)]
        for _ in range(30):
            with transports[0].stream(pair.access[0]) as first:
                with transports[1].stream(pair.access[1]) as second:
                    for i, ws in enumerate((first, second)):
                        ws.send(
                            json.dumps(
                                dict(
                                    protocolVersion="2.0",
                                    matchId=pair.mid,
                                    streamCursor=None,
                                )
                            )
                        )
                        view = pushed(ws)
                        assert view["viewerSeat"] == f"PLAYER{i + 1}"
                        assert view["matchSeq"] == 0
                        assert view["observation"]["opponent"]["hand"] is None
