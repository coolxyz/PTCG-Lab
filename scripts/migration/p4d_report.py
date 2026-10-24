"""Summarize a complete, non-skipped P4-D run without exporting private inputs."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def source_digest(root=ROOT):
    root = Path(root)
    source = sha256()
    for path in sorted((root / "apps/online-server/src").rglob("*")):
        if path.is_file() and path.suffix in (".py", ".sql", ".json"):
            source.update(
                path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes()
            )
    return source.hexdigest()


def evidence_valid(root):
    try:
        report = json.loads((Path(root) / "releases/p4d-acceptance.json").read_text())
        natural = report["naturalMatch"]
        return (
            report["status"] == "passed"
            and report["stage"] == "P4-D"
            and report["tests"] >= 76
            and report["serverSourceSha256"] == source_digest(root)
            and report["productionRoutingChanged"] is False
            and report["productionReady"] is False
            and natural["natural"] is True
            and natural["privateReplays"] == 2
            and natural["transport"] == "HTTPS/WSS"
            and len(natural["commandsBySeat"]) == 2
            and all(natural["commandsBySeat"])
            and sum(natural["commandsBySeat"]) == natural["acceptedCommands"]
            and natural["visibleFramesCompared"]
            == 2 * (natural["acceptedCommands"] + 1)
            and natural["reason"]
            not in ("unknown", "resigned", "technical_abort", "truncated")
            and all(
                report["processFailures"][name]
                == {
                    "committedBeforeLoss": name == "api-after-commit",
                    "finalMatchSeq": 1,
                    "receipts": 1,
                    "visibleFrames": 4,
                    "initialRecordUnchanged": True,
                }
                for name in (
                    "api-before-commit",
                    "rule-before-commit",
                    "api-after-commit",
                )
            )
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def generate(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / "var/p4d"):
        raise ValueError("P4-D evidence must be in its dedicated output directory")
    cases = ET.parse(directory / "results.xml").findall(".//testcase")
    if len(cases) < 76 or any(
        case.find(kind) is not None
        for case in cases
        for kind in ("failure", "error", "skipped")
    ):
        raise ValueError("Full service, real rules and image acceptance must all pass")
    names = {case.attrib["name"] for case in cases}
    required = {
        "test_two_native_players_reach_natural_end_and_replay_every_visible_frame",
        "test_real_https_wss_commands_lost_response_restart_and_private_replay",
        "test_refresh_closes_old_stream_preserves_control_and_reuse_revokes_new_token",
        "test_image_is_nonroot_api_only_and_runs_without_gui_assets",
        *(
            f"test_actual_process_death_and_unknown_response_never_reapply[{fault}]"
            for fault in ("api-before-commit", "rule-before-commit", "api-after-commit")
        ),
        *(
            f"test_expiry_during_rule_computation_cannot_commit[{kind}]"
            for kind in ("access", "session")
        ),
    }
    if not required <= names:
        raise ValueError("Missing required P4-D acceptance cases")
    natural = json.loads((directory / "natural-match.json").read_text())
    if not (
        natural["natural"] is True
        and natural["transport"] == "HTTPS/WSS"
        and natural["privateReplays"] == 2
        and all(natural["commandsBySeat"])
        and sum(natural["commandsBySeat"]) == natural["acceptedCommands"]
        and natural["visibleFramesCompared"] == 2 * (natural["acceptedCommands"] + 1)
        and natural["reason"]
        not in ("unknown", "resigned", "technical_abort", "truncated")
    ):
        raise ValueError("Incomplete natural-game evidence")
    faults = {}
    for name in ("api-before-commit", "rule-before-commit", "api-after-commit"):
        value = json.loads((directory / (name + ".json")).read_text())
        if value != {
            "committedBeforeLoss": name == "api-after-commit",
            "finalMatchSeq": 1,
            "receipts": 1,
            "visibleFrames": 4,
            "initialRecordUnchanged": True,
        }:
            raise ValueError("Incomplete process recovery evidence")
        faults[name] = value
    result = {
        "schema": 1,
        "stage": "P4-D",
        "status": "passed",
        "tests": len(cases),
        "baselineCommit": "6801d38",
        "serverSourceSha256": source_digest(),
        "naturalMatch": natural,
        "processFailures": faults,
        "productionRoutingChanged": False,
        "productionReady": False,
    }
    (directory / "P4D-REPORT.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"stage": "P4-D", "status": "passed", "tests": len(cases)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    generate(parser.parse_args().directory)
