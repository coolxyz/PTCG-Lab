"""Installed-wheel smoke, intentionally independent of repo, DB and rule assets."""

from importlib.resources import files
import sys
from ptcg_online_server.api import create_app
from ptcg_online_server.config import Settings
from ptcg_online_server.contracts import decode

settings = Settings(
    "postgresql://unreachable.invalid/db", "test", "https://api.example.test"
)
app = create_app(settings)
assert {route.path for route in app.routes} == {
    "/api/online/v2/decks",
    "/api/online/v2/rooms",
    "/api/online/v2/rooms/join",
    "/api/online/v2/rooms/{room_id}",
    "/api/online/v2/rooms/{room_id}/deck",
    "/api/online/v2/rooms/{room_id}/ready",
    "/api/online/v2/rooms/{room_id}/leave",
    "/healthz",
    "/readyz",
    "/api/online/v2/capabilities",
    "/api/online/v2/sessions",
    "/api/online/v2/sessions/current",
    "/api/online/v2/sessions/refresh",
    "/api/online/v2/sessions/revoke",
}
assert "unreachable" not in repr(settings)
assert files("ptcg_online_server").joinpath("migrations/001_sessions.sql").is_file()
assert decode("Create", b'{"protocolVersion":"2.0"}') == {"protocolVersion": "2.0"}
assert not {"apps", "packages", "ptcg", "torch", "sqlite3", "PIL"} & {
    n.split(".")[0] for n in sys.modules
}
print(
    "Installed API package, session schema and migrations verified without application or rule startup."
)
