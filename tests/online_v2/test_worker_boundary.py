"""The private rule process receives no API credentials or research paths."""

from dataclasses import replace
import json
import os
from types import SimpleNamespace
import pytest
from ptcg_online_server.config import Settings
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.worker import RuleWorker


def test_worker_drops_secret_environment_and_limits_execution(monkeypatch):
    settings = Settings(
        "postgresql://secret.invalid/db",
        "test",
        "https://test.invalid",
        rule_directory="/approved-test",
        rule_manifest_sha256="a" * 64,
        approved_rule_release_id="rules-test",
    )
    monkeypatch.setenv("PTCG_ONLINE_V2_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("UNRELATED_PRIVATE_ENV", "private-sentinel")

    def run(command, **kwargs):
        assert kwargs["timeout"] == 20
        assert set(kwargs["env"]) <= {
            "PATH",
            "SYSTEMROOT",
            "LANG",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONPATH",
        }
        request = json.loads(kwargs["input"])
        assert request["parent"] == os.getpid()
        assert set(request) == {"parent", "operation", "directory", "pin", "release"}
        assert b"secret.invalid" not in kwargs["input"]
        return SimpleNamespace(
            returncode=0, stdout=b'{"release":"rules-test","asOf":"2026-10-01"}'
        )

    monkeypatch.setattr("ptcg_online_server.worker.subprocess.run", run)
    worker = RuleWorker(settings)
    assert worker.probe()
    worker.lock.acquire()
    with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
        worker.call("probe")
    worker.lock.release()
    assert not RuleWorker(replace(settings, rule_directory=None)).probe()
