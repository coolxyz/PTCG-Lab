"""P6-E bounded resource/fairness evidence; never a P7 capacity claim."""

import argparse
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.migration.p4d_report import evidence_valid, source_digest  # noqa: E402
from scripts.migration.p6d_report import client_digest  # noqa: E402


def summarize(value):
    expected = dict(
        workload="weighted-budget-v1",
        hotWeight=2,
        coldWeight=1,
        maintenanceWeight=1,
        order=["block", "H1", "C1", "M1", "H2", "H3", "C2"],
        drained=True,
        capacityMeasured=False,
    )
    if not isinstance(value, dict) or any(
        value.get(k) != v or type(value.get(k)) is not type(v)
        for k, v in expected.items()
    ):
        raise ValueError("Incomplete or inconsistent resource evidence")
    return {k: value[k] for k in expected}


def valid(root):
    try:
        root = Path(root)
        report = json.loads((root / "releases/p6e-budgets.json").read_text())
        baseline = json.loads((root / "releases/p6d-workers.json").read_text())
        service = json.loads((root / "releases/p4d-acceptance.json").read_text())
        summarize(report["workload"])
        return (
            report["stage"] == "P6-E"
            and report["status"] == "passed"
            and report["baselineCommit"] == "f7ce35d"
            and report["baselineServerSourceSha256"] == baseline["serverSourceSha256"]
            and report["baselineClientTransportSha256"]
            == baseline["clientTransportSha256"]
            and report["serverSourceSha256"]
            == service["serverSourceSha256"]
            == source_digest(root)
            and report["clientTransportSha256"] == client_digest(root)
            and report["tests"] == service["tests"]
            and report["tests"] >= 218
            and report["capacityMeasured"] is False
            and report["productionRoutingChanged"] is False
            and evidence_valid(root)
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def generate(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / "var/p4d") or not evidence_valid(ROOT):
        raise ValueError("Current complete service acceptance required")
    cases = ET.parse(directory / "results.xml").findall(".//testcase")
    required = {
        "test_cleanup_cannot_remove_rate_bucket_between_read_and_increment",
        "test_expired_rate_key_resets_even_when_cleanup_batch_cannot_reach_it",
        "test_stale_hot_hint_is_cleared_even_when_cold_permit_is_full",
        "test_weighted_environment_rollback_is_explicit",
        "test_weighted_hot_cold_and_maintenance_rotate_without_starvation",
        "test_maintenance_queue_is_bounded_and_weighted_rollback_keeps_fifo",
        "test_worker_cache_hints_are_bounded_generation_scoped_and_advisory",
        "test_replay_inflight_reserves_http_capacity_and_websockets_do_not_consume_it",
        "test_transport_ceiling_includes_http_streams_and_keepalive_reserve",
        "test_send_burst_rate_and_oversize_are_bounded_without_retaining_payloads",
        "test_throttled_stream_rechecks_revocation_and_keeps_small_error_allowance",
        "test_handshake_admission_releases_every_slot_on_cancel",
        "test_replay_and_stream_byte_pages_preserve_cursors_and_private_history",
        "test_maintenance_batches_expiry_without_deleting_replay_or_refresh_history",
        "test_visible_reads_do_not_load_authoritative_recovery",
        "test_stream_budget_exhaustion_reports_error_and_reconnect_uses_durable_cursor",
        "test_maintenance_skips_locked_expired_rows",
        "test_production_process_full_stream_slots_leave_http_command_capacity",
        *(
            f"test_cold_permit_waits_without_blocking_other_lane_hot_work[{v}]"
            for v in ("False", "True")
        ),
        *(
            f"test_replay_rate_is_durable_and_does_not_exhaust_command_allowance[{v}]"
            for v in ("peer", "global")
        ),
        *(
            f"test_real_cached_candidate_sql_failure_or_lost_ack_never_reapplies[{v}]"
            for v in ("rollback", "ack_lost")
        ),
    }
    if not required <= {case.attrib["name"] for case in cases} or any(
        case.find(kind) is not None
        for case in cases
        for kind in ("failure", "error", "skipped")
    ):
        raise ValueError("Complete passing budget and failure acceptance required")
    service = json.loads((ROOT / "releases/p4d-acceptance.json").read_text())
    if service["tests"] != len(cases) or len(cases) < 218:
        raise ValueError("Service and resource acceptance runs differ")
    baseline = json.loads((ROOT / "releases/p6d-workers.json").read_text())
    result = dict(
        schema=1,
        stage="P6-E",
        status="passed",
        tests=len(cases),
        serverSourceSha256=source_digest(),
        clientTransportSha256=client_digest(),
        baselineCommit="f7ce35d",
        baselineServerSourceSha256=baseline["serverSourceSha256"],
        baselineClientTransportSha256=baseline["clientTransportSha256"],
        workload=summarize(json.loads((directory / "p6e-scheduling.json").read_text())),
        capacityMeasured=False,
        productionRoutingChanged=False,
        scope="Deterministic weighted scheduling, bounded bytes/pages/maintenance and separate HTTP/WSS admission; real SQL rollback, lost cache acknowledgements and full HTTPS/WSS regression. P7 capacity matrix not run.",
    )
    (directory / "P6E-REPORT.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"stage": "P6-E", "status": "passed", "tests": len(cases)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    generate(parser.parse_args().directory)
