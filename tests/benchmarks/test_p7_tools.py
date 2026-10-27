"""Safety and evidence checks for P7 tools; no Docker or private assets needed."""

import json
import threading

import pytest

from benchmarks.online import lab, guard
from benchmarks.online.driver import validate_run
from benchmarks.online.measure import Histogram, histogram_quantile, latency
from benchmarks.online.resources import group


@pytest.mark.parametrize(
    "name",
    [
        "/",
        "../docker",
        "docker/../x",
        "ptcg-p7-user-service",
        "ptcg-p7-123456789abc/child",
    ],
)
def test_resource_operations_reject_shared_or_traversing_names(tmp_path, name):
    with pytest.raises(ValueError):
        group(tmp_path, name)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "label,matches,warmup,steady",
    [
        ("matrix", 10, 10, 60),
        ("matrix", 10, 299, 1200),
        ("matrix", 10, 300, 1199),
        ("matrix", 1, 300, 1200),
        ("soak", 10, 300, 7199),
        ("pilot", 1000, 0, 60),
    ],
)
def test_short_runs_cannot_claim_formal_or_soak_measurement(
    label, matches, warmup, steady
):
    with pytest.raises(ValueError):
        validate_run(label, matches, warmup, steady)


def test_histogram_uses_conservative_upper_bounds_and_separates_client_quantiles():
    h = Histogram()
    for value in (0.01, 0.04, 0.1, 0.42, 1.1):
        h.observe(value)
    result = h.snapshot()
    assert sum(result["buckets"]) == result["count"] == 5
    assert histogram_quantile([result], 0.95) == 1.5
    assert latency([0.01, 0.04, 0.1, 0.42, 1.1])["p95"] == 1.1
    huge = Histogram()
    huge.observe(61)
    assert histogram_quantile([huge.snapshot()], 0.99) is None
    assert latency([])["p95"] is None


def test_container_actions_refuse_protected_or_mislabelled_services(monkeypatch):
    value = {"project": "ptcg-p7-123456789abc"}
    for labels in (
        {},
        {lab.LABEL: value["project"], "com.docker.compose.service": "database"},
    ):
        monkeypatch.setattr(
            lab, "docker", lambda *a, **k: json.dumps([{"Config": {"Labels": labels}}])
        )
        with pytest.raises(AssertionError):
            lab.inspect_owned(value, "api")


def test_lab_metadata_rejects_paths_outside_private_run_root(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, "ROOT", tmp_path)
    with pytest.raises(ValueError):
        lab.metadata(tmp_path / "other")
    owned = tmp_path / "var/p7/test"
    owned.mkdir(parents=True)
    (owned / "lab.json").write_text(
        json.dumps({"schema": 1, "project": "existing-lan"})
    )
    with pytest.raises(ValueError):
        lab.metadata(owned)


def test_guard_stops_only_injected_workload_on_target_oom(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "health", lambda rows: [0.01])
    stopped = threading.Event()
    sample = dict(
        cpuMax="200000 100000",
        memoryMax=2 * 1024**3,
        swapBytes=0,
        memoryEvents={"oom_kill": 1},
    )
    monitor = guard.Guard(tmp_path, [{"synthetic": True}], lambda: sample, stopped.set)
    monitor.thread.start()
    assert stopped.wait(2)
    monitor.thread.join(2)
    assert monitor.error == "AssertionError"
    # The guard receives only a stop-workload callback, no restart/update callback.
    with pytest.raises(ValueError, match="P7_GUARD_STOPPED_WORKLOAD"):
        monitor.__exit__(None, None, None)
    assert (
        json.loads((tmp_path / "guard-report.json").read_text())["status"] == "failed"
    )


def test_owned_cgroup_name_is_a_child_never_the_root(tmp_path):
    assert group(tmp_path, "ptcg-p7-123456789abc").parent == tmp_path
