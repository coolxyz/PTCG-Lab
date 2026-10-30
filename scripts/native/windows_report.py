"""Require actual native Windows component execution; skipped tests are not proof."""

import argparse
import json
import os
from pathlib import Path
import platform
import sys
import xml.etree.ElementTree as ET

REQUIRED = {
    "test_real_dpapi_encrypted_journal_reopens_in_another_process",
    "test_real_handle_lock_excludes_a_second_process",
    "test_real_dpapi_tamper_and_directory_move_preserve_original_bytes",
    "test_real_windows_acl_rejects_everyone_without_repair",
    "test_real_windows_hardlink_and_junction_are_rejected",
    "test_real_windows_failed_replace_poison_owner_and_keep_journal",
    "test_real_windows_killed_owner_releases_lock_and_preserves_pending",
    "test_windows_host_uses_acl_checks_instead_of_posix_modes",
    "test_real_windows_identity_handoff_and_native_import_preserve_journal",
    "test_real_windows_identity_refuses_running_target_and_unsafe_destination",
    "test_real_windows_unicode_and_long_private_path_reopens_exact_pending",
    "test_real_windows_first_launch_accepts_only_private_webview_cache",
}


def summarize(path):
    cases = ET.parse(path).findall(".//testcase")
    names = [c.attrib["name"] for c in cases]
    if (
        len(names) != len(set(names))
        or not REQUIRED <= set(names)
        or any(
            c.find(kind) is not None
            for c in cases
            for kind in ("failure", "error", "skipped")
        )
    ):
        raise ValueError("Native Windows component tests incomplete or unsuccessful")
    return len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        sys.platform != "win32"
        or os.name != "nt"
        or platform.machine().lower() not in {"amd64", "x86_64"}
        or os.environ.get("PTCG_WINDOWS_NATIVE_TEST") != "1"
    ):
        raise SystemExit("Native Windows x64 required; WSL2 is not acceptance")
    count = summarize(args.junit)
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from scripts.packaging.source_identity import source_digest

    report = {
        "schema": 1,
        "stage": "P8-B-components",
        "status": "passed",
        "tests": count,
        "os": platform.platform(),
        "python": platform.python_version(),
        "sourceSha256": source_digest(root),
        "nativeGuiAccepted": False,
        "jobObjectAccepted": False,
        "releaseReady": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        "Native Windows storage components passed; full P8-B acceptance remains pending."
    )


if __name__ == "__main__":
    main()
