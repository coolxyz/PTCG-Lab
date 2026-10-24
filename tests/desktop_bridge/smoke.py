"""Installed bridge outside checkout: no engine, database, assets or service startup."""

from pathlib import Path
import sys
import tempfile
from ptcg_desktop_api.api import create_app
from ptcg_desktop_api.bridge import Bridge
from ptcg_online_client.transport import Server

with tempfile.TemporaryDirectory() as directory:
    owner = Bridge(
        {"offline": Server("https://offline.example.test")}, Path(directory) / "private"
    )
    try:
        token = owner.issue_window()
        app = create_app(owner, origin="http://127.0.0.1:18785")
        assert owner.list_profiles(token)[0]["id"] == "offline"
        assert owner.call(token, "offline", "status", {})["identity"] is None
    finally:
        owner.close()
assert not {
    "ptcg_online_server",
    "ptcg_core",
    "ptcg_engine_provider",
    "ptcg",
    "apps",
    "packages",
    "torch",
    "sqlite3",
    "psycopg",
} & {m.split(".")[0] for m in sys.modules}
print(
    "Native bridge wheel and private owner verified outside checkout without service startup."
)
