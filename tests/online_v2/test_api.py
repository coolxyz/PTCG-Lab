from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from ptcg_protocol.validation import validate
from ptcg_online_server.api import create_app

PREFIX = "/api/online/v2"


def client(settings):
    return TestClient(create_app(settings), base_url=settings.public_origin)


def test_no_legacy_routes_browser_auth_or_credential_query(repo):
    with client(repo.settings) as c:
        for path in (
            "/",
            "/api/battle/session",
            "/api/cards",
            "/api/collection",
            "/api/sync/status",
            "/api/ai",
            "/docs",
            "/openapi.json",
            "/card-images/test",
            "/api/online/v1/capabilities",
        ):
            assert c.get(path).status_code == 404
        for headers in (
            {"Origin": "https://evil.test"},
            {"Origin": "null"},
            {"Origin": repo.settings.public_origin},
            {"Cookie": "legacy=secret"},
            {"Host": "evil.test"},
        ):
            r = c.post(
                PREFIX + "/sessions", json={"protocolVersion": "2.0"}, headers=headers
            )
            assert r.status_code == 403
            assert "access-control-allow-origin" not in r.headers
        r = c.get(PREFIX + "/sessions/current?access_token=secret-sentinel")
        assert r.status_code == 400 and "secret-sentinel" not in r.text
        with pytest.raises(Exception):
            with c.websocket_connect(PREFIX + "/stream?access_token=secret"):
                pass


@pytest.mark.parametrize(
    "body,code",
    [
        ('{"protocolVersion":"2.0","protocolVersion":"2.0"}', "INVALID_JSON"),
        ('{"protocolVersion":"2.0","name":NaN}', "INVALID_JSON"),
        ('{"protocolVersion":"1.0"}', "UNSUPPORTED_PROTOCOL"),
        ('{"protocolVersion":"2.0","userId":"forged"}', "INVALID_PAYLOAD"),
        ('{"protocolVersion":"2.0","name":"line\\n"}', "INVALID_PAYLOAD"),
        ('{"protocolVersion":"2.0","name":""}', "INVALID_PAYLOAD"),
        ("[]", "INVALID_PAYLOAD"),
    ],
)
def test_request_contract_rejects_ambiguous_and_untrusted_fields(repo, body, code):
    with client(repo.settings) as c:
        r = c.post(
            PREFIX + "/sessions",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code in (400, 422)
        assert validate("ProtocolError", r.json())["code"] == code
        assert "body" not in r.text


def test_session_creation_limit_cannot_be_bypassed_by_forwarded_header(repo):
    with client(replace(repo.settings, create_per_minute=1)) as c:
        assert (
            c.post(PREFIX + "/sessions", json={"protocolVersion": "2.0"}).status_code
            == 201
        )
        for ip in ("198.51.100.1", "198.51.100.2"):
            r = c.post(
                PREFIX + "/sessions",
                json={"protocolVersion": "2.0"},
                headers={"X-Forwarded-For": ip},
            )
            assert r.status_code == 429
            assert r.headers["retry-after"] == "60"
    with client(replace(repo.settings, create_per_minute=1)) as restarted:
        assert (
            restarted.post(
                PREFIX + "/sessions", json={"protocolVersion": "2.0"}
            ).status_code
            == 429
        )


def test_no_rules_reports_failed_readiness_and_no_game_features(repo):
    with client(repo.settings) as c:
        assert c.get("/healthz").json() == {"status": "alive"}
        ready = c.get("/readyz")
        assert ready.status_code == 503
        assert ready.json() == {"ready": False, "database": True, "rules": False}
        caps = c.get(PREFIX + "/capabilities")
        assert caps.status_code == 200
        assert validate("Capabilities", caps.json())["ruleReleaseIds"] == []
        assert (
            caps.json()["acceptingMatches"] is False and caps.json()["features"] == []
        )
        assert "card" not in caps.text.lower()
        assert (
            c.post(PREFIX + "/rooms", json={"protocolVersion": "2.0"}).status_code
            == 404
        )


def test_missing_migration_stays_unready_and_does_not_auto_install(settings):
    with client(settings) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/readyz").json()["database"] is False
        response = c.post(PREFIX + "/sessions", json={"protocolVersion": "2.0"})
        assert response.status_code == 503
        assert response.json() == {
            "protocolVersion": "2.0",
            "code": "INTERNAL_ERROR",
            "retryable": True,
        }


def test_large_body_compressed_body_and_invalid_auth_do_not_leak(repo):
    with client(repo.settings) as c:
        r = c.post(
            PREFIX + "/sessions",
            content=b"x" * 16385,
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 413
        r = c.post(
            PREFIX + "/sessions",
            content=b"x",
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )
        assert r.status_code == 415
        for header in (
            "Bearer secret-sentinel",
            "Basic secret-sentinel",
            "Bearer atk_" + "a" * 43,
        ):
            r = c.get(PREFIX + "/sessions/current", headers={"Authorization": header})
            assert r.status_code == 401
            assert "secret-sentinel" not in r.text and "www-authenticate" in r.headers
            assert "no-store" in r.headers["cache-control"]
