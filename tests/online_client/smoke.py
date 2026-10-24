"""Run from an installed wheel outside checkout; no server or assets imported."""

from importlib.resources import files
from pathlib import Path
import sys
import tempfile
from ptcg_online_client.client import Client
from ptcg_online_client.contracts import checked
from ptcg_online_client.transport import Server


class NoNetwork:
    def request(self, *_):
        raise AssertionError("Package smoke must never contact a service")


assert files("ptcg_online_client").joinpath("schemas/match.schema.json").is_file()
assert checked("Create", {"protocolVersion": "2.0"}) == {"protocolVersion": "2.0"}
with tempfile.TemporaryDirectory() as directory:
    with Client(
        Server("https://offline.example.test"),
        Path(directory) / "credentials",
        transport=NoNetwork(),
    ) as client:
        assert client.status()["identity"] is None
assert not {
    "apps",
    "packages",
    "ptcg",
    "ptcg_core",
    "ptcg_engine_provider",
    "fastapi",
    "psycopg",
    "torch",
    "sqlite3",
    "PIL",
} & {n.split(".")[0] for n in sys.modules}
print(
    "Native client wheel, schemas and private vault verified without network, engine or application startup."
)
