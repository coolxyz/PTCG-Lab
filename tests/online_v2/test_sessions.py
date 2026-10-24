from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import pytest
from fastapi.testclient import TestClient
from ptcg_online_server.api import create_app
from ptcg_online_server.auth import Sessions
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.repository import Repository

PREFIX = "/api/online/v2"


def client(settings):
    return TestClient(create_app(settings), base_url=settings.public_origin)


def token(repo):
    return Sessions(repo).create(name="玩家", device_name="测试电脑")


def auth(result):
    return {"Authorization": "Bearer " + result["accessToken"]}


def test_two_identities_rotate_and_revoke_independently(repo):
    with client(repo.settings) as c:
        first = c.post(
            PREFIX + "/sessions", json={"protocolVersion": "2.0", "name": "甲"}
        )
        assert first.status_code == 201
        assert "set-cookie" not in first.headers
        a = first.json()
        b = c.post(
            PREFIX + "/sessions", json={"protocolVersion": "2.0", "name": "乙"}
        ).json()
        assert a["userId"] != b["userId"] and a["deviceId"] != b["deviceId"]
        assert (
            c.get(PREFIX + "/sessions/current", headers=auth(a)).json()["userId"]
            == a["userId"]
        )
        rotated = c.post(
            PREFIX + "/sessions/refresh",
            json={"protocolVersion": "2.0", "refreshToken": a["refreshToken"]},
        )
        assert rotated.status_code == 200
        new = rotated.json()
        assert new["sessionId"] == a["sessionId"]
        assert new["refreshToken"] != a["refreshToken"]
        assert new["refreshExpiresAt"] == a["refreshExpiresAt"]
        assert c.get(PREFIX + "/sessions/current", headers=auth(a)).status_code == 401
        for _ in range(2):
            assert (
                c.post(
                    PREFIX + "/sessions/revoke",
                    headers=auth(new),
                    json={"protocolVersion": "2.0"},
                ).status_code
                == 200
            )
        assert c.get(PREFIX + "/sessions/current", headers=auth(new)).status_code == 401
        assert c.get(PREFIX + "/sessions/current", headers=auth(b)).status_code == 200
        assert (
            c.post(
                PREFIX + "/sessions/refresh",
                json={"protocolVersion": "2.0", "refreshToken": new["refreshToken"]},
            ).status_code
            == 401
        )


def test_rotation_race_commits_family_revocation(repo):
    service = Sessions(repo)
    a = token(repo)

    def rotate():
        try:
            return service.refresh(a["refreshToken"])
        except ServiceError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: rotate(), range(2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert results.count("UNAUTHORIZED") == 1
    new = next(r for r in results if isinstance(r, dict))
    with pytest.raises(ServiceError, match="UNAUTHORIZED"):
        service.authenticate(new["accessToken"])
    with repo.transaction() as db:
        assert (
            db.execute(
                "SELECT revoked_at FROM ptcg_v2.sessions WHERE id=%s", (a["sessionId"],)
            ).fetchone()["revoked_at"]
            is not None
        )
        assert (
            db.execute("SELECT count(*) AS n FROM ptcg_v2.refresh_tokens").fetchone()[
                "n"
            ]
            == 2
        )


def test_expiry_and_access_refresh_type_confusion_are_rejected(repo):
    service = Sessions(repo)
    a = token(repo)
    with pytest.raises(ServiceError):
        service.refresh(a["accessToken"])
    with pytest.raises(ServiceError):
        service.authenticate(a["refreshToken"])
    with repo.transaction() as db:
        db.execute(
            "UPDATE ptcg_v2.access_tokens SET expires_at=now()-interval '1 second'"
        )
    with pytest.raises(ServiceError):
        service.authenticate(a["accessToken"])
    new = service.refresh(a["refreshToken"])
    with repo.transaction() as db:
        db.execute("UPDATE ptcg_v2.sessions SET expires_at=now()-interval '1 second'")
    with pytest.raises(ServiceError):
        service.refresh(new["refreshToken"])


def test_no_raw_credentials_in_storage_and_instances_share_revocation(repo):
    a = token(repo)
    with repo.transaction() as db:
        values = []
        for table in (
            "access_tokens",
            "refresh_tokens",
            "sessions",
            "users",
            "devices",
        ):
            values.extend(db.execute("SELECT * FROM ptcg_v2." + table).fetchall())
    raw = json.dumps(values, default=str)
    assert a["accessToken"] not in raw and a["refreshToken"] not in raw
    second = Sessions(Repository(repo.settings))
    assert second.authenticate(a["accessToken"])["userId"] == a["userId"]
    second.revoke(a["accessToken"])
    with pytest.raises(ServiceError):
        Sessions(Repository(repo.settings)).authenticate(a["accessToken"])


def test_guest_admission_limit_serializes_concurrent_creation(repo):
    service = Sessions(Repository(replace(repo.settings, max_sessions=1)))

    def create():
        try:
            return service.create(name="new", device_name="device")
        except ServiceError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: create(), range(2)))
    assert results.count("CAPACITY_EXCEEDED") == 1
    with repo.transaction() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM ptcg_v2.users").fetchone()["n"] == 1
        )


def test_rates_survive_new_repository_and_bound_distinct_peers(repo):
    settings = replace(repo.settings, create_per_minute=1, max_rate_keys=3)
    one, two = Repository(settings), Repository(settings)
    one.rate("peer-one", creating=True)
    with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
        two.rate("peer-one", creating=True)
    with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
        two.rate("peer-two")
    with repo.transaction() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM ptcg_v2.rate_buckets").fetchone()["n"]
            == 3
        )
        db.execute("UPDATE ptcg_v2.rate_buckets SET reset_at=now()-interval '1 second'")
    two.rate("peer-two", creating=True)


def test_rotation_failure_rolls_back_both_consumption_and_new_tokens(repo, monkeypatch):
    service = Sessions(repo)
    original = service._issue
    first = token(repo)

    def fail_after_issue(db, session):
        original(db, session)
        raise RuntimeError("simulated transaction abort")

    monkeypatch.setattr(service, "_issue", fail_after_issue)
    with pytest.raises(RuntimeError):
        service.refresh(first["refreshToken"])
    assert service.authenticate(first["accessToken"])["sessionId"] == first["sessionId"]
    monkeypatch.setattr(service, "_issue", original)
    assert service.refresh(first["refreshToken"])["sessionId"] == first["sessionId"]
    with repo.transaction() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM ptcg_v2.refresh_tokens").fetchone()[
                "n"
            ]
            == 2
        )
