"""Native Windows component checks; never substitute a cross-check for execution."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = {
    "exports::tests::downloads_only_allow_owned_export_endpoints_and_fixed_filenames",
    "backend::tests::ready_is_bound_to_owned_pid_nonce_and_loopback",
    "backend::tests::payload_rejects_tamper_extras_links_and_path_escape",
    "platform::instance::tests::windows_focus_competition_and_killed_owner_recovery",
    "platform::instance::tests::windows_malformed_or_idle_focus_peer_cannot_block_drop",
    "platform::process::tests::windows_arguments_preserve_quotes_spaces_and_trailing_slashes",
    "platform::process::tests::windows_job_cleanup_kills_descendants_and_preserves_unrelated_process",
    "platform::process::tests::windows_owner_crash_closes_job_and_reaps_process_tree",
    "platform::security::tests::windows_private_profile_rejects_foreign_acl_without_repair",
    "platform::security::tests::windows_profile_rejects_hardlinked_lock_and_junction_ancestor",
    "platform::instance::tests::windows_instance_owner_helper",
    "platform::process::tests::windows_grandchild_helper",
    "platform::process::tests::windows_job_child_helper",
    "platform::process::tests::windows_job_owner_helper",
    "acceptance::tests::operator_mode_refuses_real_or_configured_profiles_and_reuses_only_its_marker",
    "acceptance::tests::operator_frames_and_renderer_reports_cannot_add_authority_or_skip_checks",
    "acceptance::tests::network_operator_accepts_only_local_tls_and_its_own_trust_file",
    "tests::navigation_is_limited_to_bootstrap_and_verified_product_origin",
}


def summarize(text):
    passed = set(re.findall(r"^test (\S+) \.\.\. ok\s*$", text, flags=re.M))
    result = re.findall(
        r"test result: ok\. (\d+) passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;",
        text,
    )
    if (
        not REQUIRED <= passed
        or not result
        or len(re.findall(r"^test result:", text, flags=re.M)) != len(result)
        or sum(map(int, result)) != len(passed)
    ):
        raise ValueError("Windows Rust components incomplete or unsuccessful")
    return len(passed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        sys.platform != "win32"
        or os.name != "nt"
        or platform.machine().lower() not in {"amd64", "x86_64"}
        or os.environ.get("PTCG_WINDOWS_NATIVE_TEST") != "1"
    ):
        raise SystemExit(
            "Native Windows x64 required; WSL2/cross-check is not acceptance"
        )
    version = subprocess.check_output(["rustc", "--version"], text=True)
    if not version.startswith("rustc 1.90.0 "):
        raise SystemExit("Use the locked Rust 1.90.0 toolchain")
    manifest = ROOT / "apps/native-shell/src-tauri/Cargo.toml"
    command = [
        "cargo",
        "--color",
        "never",
        "test",
        "--locked",
        "--manifest-path",
        str(manifest),
    ]
    run = subprocess.run(
        command + ["--", "--test-threads=1"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="strict",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".log").write_text(run.stdout, encoding="utf-8")
    run.check_returncode()
    count = summarize(run.stdout)
    # Includes the actual Tauri binary, Windows-only WebView2 setup and SDK icon
    # resources. Component success alone must not conceal a non-building shell.
    subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "--locked",
            "--manifest-path",
            str(manifest),
            "--bin",
            "ptcg-native-shell",
        ],
        cwd=ROOT,
        check=True,
    )
    sys.path.insert(0, str(ROOT))
    from scripts.packaging.source_identity import source_digest

    report = {
        "schema": 1,
        "stage": "P8-B-shell-components",
        "status": "passed",
        "platform": platform.platform(),
        "tests": count,
        "rust": version.strip(),
        "sourceSha256": source_digest(ROOT),
        "nativeShellCompile": "passed",
        "releaseBuild": "passed",
        "jobObjectComponents": "passed",
        "nativeGuiAccepted": False,
        "releaseReady": False,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        "Windows shell components passed; interactive WebView2 acceptance remains pending."
    )


if __name__ == "__main__":
    main()
