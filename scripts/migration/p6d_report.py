"""Publish persistent-worker equivalence with current full service acceptance."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.migration.p4d_report import evidence_valid, source_digest  # noqa: E402


def client_digest(root=ROOT):
    digest = sha256()
    base = Path(root) / "packages/online-client/src/ptcg_online_client"
    for name in ("__init__.py", "transport.py", "websocket.py"):
        digest.update(name.encode() + b"\0" + (base / name).read_bytes())
    return digest.hexdigest()


def summarize(value):
    expected = dict(
        workload="persistent-interleaved-v1",
        workers=2,
        matches=3,
        commandsPerMatch=5,
        comparedViews=30,
        hotLoads=2,
        hotRestores=3,
        coldRestores=30,
        sameSeedEquivalent=True,
        uncommittedCandidateDiscarded=True,
        capacityMeasured=False,
    )
    if not isinstance(value, dict) or any(
        value.get(k) != v or type(value.get(k)) is not type(v)
        for k, v in expected.items()
    ):
        raise ValueError("Incomplete or inconsistent worker evidence")
    return {k: value[k] for k in expected}


def valid(root, *, historical=False):
    try:
        root = Path(root)
        report = json.loads((root / "releases/p6d-workers.json").read_text())
        baseline = json.loads((root / "releases/p6c-scheduler.json").read_text())
        service = json.loads((root / "releases/p4d-acceptance.json").read_text())
        summarize(report["workload"])
        if historical:
            successor = json.loads((root / "releases/p6e-budgets.json").read_text())
            source_valid = (
                successor["baselineCommit"] == "f7ce35d"
                and report["serverSourceSha256"]
                == successor["baselineServerSourceSha256"]
                and report["clientTransportSha256"]
                == successor["baselineClientTransportSha256"]
            )
        else:
            source_valid = (
                report["serverSourceSha256"]
                == service["serverSourceSha256"]
                == source_digest(root)
                and report["clientTransportSha256"] == client_digest(root)
                and report["tests"] == service["tests"]
                and evidence_valid(root)
            )
        return (
            report["stage"] == "P6-D"
            and report["status"] == "passed"
            and report["baselineCommit"] == "691fc0a"
            and report["baselineServerSourceSha256"] == baseline["serverSourceSha256"]
            and source_valid
            and report["tests"] >= 182
            and report["capacityMeasured"] is False
            and report["productionRoutingChanged"] is False
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def generate(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / "var/p4d") or not evidence_valid(ROOT):
        raise ValueError("Current complete service acceptance required")
    cases = ET.parse(directory / "results.xml").findall(".//testcase")
    required = {
        "test_native_tls_repeated_dual_handshakes_keep_trust_and_private_views",
        "test_persistent_interleaved_matches_equal_cold_restore_and_uncommitted_candidates",
        "test_worker_death_recovery_is_per_lane_and_cold_budget_preserves_hot_work",
        "test_real_restore_timeout_reaps_only_its_worker_generation",
        "test_two_worker_lanes_run_independently_but_each_match_stays_serial",
    }
    required |= {
        *(
            f"test_worker_drops_secret_environment_and_limits_execution[{kind}]"
            for kind in ("False", "True")
        ),
        "test_worker_rss_watermark_recycles_process_after_response",
        "test_runtime_lru_idle_record_budget_and_restore_deadline",
        *(
            f"test_pipe_limits_and_stale_generation_results_are_rejected[{kind}]"
            for kind in ("generation", "requestId", "oversize", "timeout")
        ),
        *(
            f"test_cache_confirmation_observes_sql_commit_or_rollback[{kind}]"
            for kind in ("False", "True")
        ),
    }
    names = {case.attrib["name"] for case in cases}
    if not required <= names or any(
        case.find(kind) is not None
        for case in cases
        for kind in ("failure", "error", "skipped")
    ):
        raise ValueError("Complete passing worker acceptance required")
    service = json.loads((ROOT / "releases/p4d-acceptance.json").read_text())
    if service["tests"] != len(cases) or len(cases) < 182:
        raise ValueError("Service and worker acceptance runs differ")
    baseline = json.loads((ROOT / "releases/p6c-scheduler.json").read_text())
    measured = json.loads((directory / "p6d-workers.json").read_text())
    result = dict(
        schema=1,
        stage="P6-D",
        status="passed",
        tests=len(cases),
        serverSourceSha256=source_digest(),
        clientTransportSha256=client_digest(),
        baselineCommit="691fc0a",
        baselineServerSourceSha256=baseline["serverSourceSha256"],
        workload=summarize(measured),
        capacityMeasured=False,
        productionRoutingChanged=False,
        scope="Three real-rule matches interleaved over two persistent workers; cold/hot equivalence, bounded cache and generation failures. Full HTTPS/WSS regression; no capacity or latency SLA.",
    )
    (directory / "P6D-REPORT.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"stage": "P6-D", "status": "passed", "tests": len(cases)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    generate(parser.parse_args().directory)
