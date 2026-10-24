"""Explicit deployment inputs; no research paths, automatic fallback or I/O at import."""

from dataclasses import dataclass, field
import os
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    server_id: str
    public_origin: str
    allow_loopback_http: bool = False
    access_seconds: int = 900
    refresh_seconds: int = 2592000
    max_sessions: int = 1000
    create_per_minute: int = 10
    requests_per_minute: int = 240
    global_per_minute: int = 1200
    max_rate_keys: int = 4096
    max_inflight: int = 32
    body_timeout: float = 10
    join_per_minute: int = 20
    max_rooms: int = 100
    max_decks_per_user: int = 100
    max_lobby_receipts: int = 2000
    room_seconds: int = 900
    rule_directory: str | None = field(default=None, repr=False)
    rule_manifest_sha256: str | None = None
    approved_rule_release_id: str | None = None

    def __post_init__(self):
        origin = urlsplit(self.public_origin)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.path
            or self.public_origin != f"{origin.scheme}://{origin.netloc}"
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.server_id)
        ):
            raise ValueError("INVALID_DEPLOYMENT_CONFIG")
        if origin.scheme == "http" and not (
            self.allow_loopback_http
            and origin.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("HTTPS_REQUIRED")
        if not self.database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("POSTGRES_REQUIRED")
        for value in (
            self.access_seconds,
            self.refresh_seconds,
            self.max_sessions,
            self.create_per_minute,
            self.requests_per_minute,
            self.global_per_minute,
            self.max_rate_keys,
            self.max_inflight,
            self.join_per_minute,
            self.max_rooms,
            self.max_decks_per_user,
            self.max_lobby_receipts,
            self.room_seconds,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("INVALID_DEPLOYMENT_LIMIT")
        if (
            not 1 <= self.access_seconds <= min(3600, self.refresh_seconds)
            or self.refresh_seconds > 2592000
        ):
            raise ValueError("INVALID_TOKEN_LIFETIME")
        if not 0 < self.body_timeout <= 30:
            raise ValueError("INVALID_BODY_TIMEOUT")

    @property
    def host(self):
        return urlsplit(self.public_origin).netloc.lower()

    @classmethod
    def from_env(cls):
        def env(key, default=None):
            return os.environ.get("PTCG_ONLINE_V2_" + key, default)

        dsn = env("DATABASE_URL")
        secret_file = env("DATABASE_URL_FILE")
        if bool(dsn) == bool(secret_file):
            raise ValueError("EXACTLY_ONE_DATABASE_SECRET_REQUIRED")
        if secret_file:
            from pathlib import Path

            try:
                dsn = Path(secret_file).read_text().strip()
            except OSError:
                raise ValueError("DATABASE_SECRET_UNAVAILABLE") from None
        try:
            return cls(
                database_url=dsn,
                server_id=env("SERVER_ID", ""),
                public_origin=env("PUBLIC_ORIGIN", ""),
                allow_loopback_http=env("ALLOW_LOOPBACK_HTTP") == "1",
                access_seconds=int(env("ACCESS_SECONDS", "900")),
                refresh_seconds=int(env("REFRESH_SECONDS", "2592000")),
                max_sessions=int(env("MAX_SESSIONS", "1000")),
                create_per_minute=int(env("CREATE_PER_MINUTE", "10")),
                requests_per_minute=int(env("REQUESTS_PER_MINUTE", "240")),
                global_per_minute=int(env("GLOBAL_PER_MINUTE", "1200")),
                max_rate_keys=int(env("MAX_RATE_KEYS", "4096")),
                max_inflight=int(env("MAX_INFLIGHT", "32")),
                join_per_minute=int(env("JOIN_PER_MINUTE", "20")),
                max_rooms=int(env("MAX_ROOMS", "100")),
                max_decks_per_user=int(env("MAX_DECKS_PER_USER", "100")),
                max_lobby_receipts=int(env("MAX_LOBBY_RECEIPTS", "2000")),
                room_seconds=int(env("ROOM_SECONDS", "900")),
                rule_directory=env("RULE_DIRECTORY"),
                rule_manifest_sha256=env("RULE_MANIFEST_SHA256"),
                approved_rule_release_id=env("APPROVED_RULE_RELEASE_ID"),
            )
        except (ValueError, TypeError):
            raise ValueError("INVALID_DEPLOYMENT_CONFIG") from None
