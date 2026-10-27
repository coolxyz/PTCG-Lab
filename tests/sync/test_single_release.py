from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from packages.sync.common import SyncError
from packages.sync.runtime import RuntimePool, Worker


def test_only_active_release_can_start_a_worker(tmp_path, monkeypatch):
    spawn = Mock()
    monkeypatch.setattr("packages.sync.runtime.Worker", spawn)
    releases = SimpleNamespace(current=lambda: "current")
    pool = RuntimePool(releases, tmp_path / "unused.sqlite")
    with pytest.raises(SyncError) as error:
        pool.get("previous")
    assert error.value.code == "RULE_RELEASE_UNAVAILABLE"
    spawn.assert_not_called()


def test_pre_policy_snapshot_cannot_execute_even_when_it_is_active(tmp_path, monkeypatch):
    start = Mock()
    monkeypatch.setattr("packages.sync.runtime.subprocess.Popen", start)
    releases = SimpleNamespace(verify=lambda release: {"metadata": {}})
    with pytest.raises(SyncError) as error:
        Worker(releases, "previous-policy", tmp_path / "unused.sqlite")
    assert error.value.code == "RULE_RELEASE_UNAVAILABLE"
    start.assert_not_called()


def test_activation_change_evicts_the_previous_worker(tmp_path, monkeypatch):
    old = SimpleNamespace(close=Mock())
    active = SimpleNamespace(process=SimpleNamespace(poll=lambda: None), close=Mock())
    monkeypatch.setattr("packages.sync.runtime.Worker", lambda *args: active)
    pool = RuntimePool(SimpleNamespace(current=lambda: "current"), tmp_path / "unused.sqlite")
    pool.workers["previous"] = old
    assert pool.get("current") is active
    old.close.assert_called_once()
    assert set(pool.workers) == {"current"}
    pool.close()
