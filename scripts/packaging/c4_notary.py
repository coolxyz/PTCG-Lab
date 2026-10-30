"""Offline notarization command plan. This module NEVER uploads an artifact."""

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.packaging.common import new_output, record  # noqa: E402


def plan(app, dmg, *, profile):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", profile):
        raise ValueError("A Keychain profile label is required, never a password/path")
    app, dmg = Path(app).absolute(), Path(dmg).absolute()
    zipped = app.parent / "Developer-ID-notary-input.zip"
    # APP first: staple its ticket before the final DMG is built. DMG second:
    # the final image gets its own submission/ticket; a ZIP cannot be stapled.
    return {
        "schema": 1,
        "status": "plan-only",
        "externalApprovalRequired": True,
        "executed": False,
        "prerequisites": [
            "explicit-Developer-ID-certificate-and-team",
            "all-nested-binaries-hardened-and-timestamped",
            "local-signature-and-payload-verification",
            "explicit-authorization-to-upload-to-Apple",
        ],
        "appStage": [
            ["/usr/bin/ditto", "-c", "-k", "--keepParent", str(app), str(zipped)],
            [
                "/usr/bin/xcrun",
                "notarytool",
                "submit",
                str(zipped),
                "--keychain-profile",
                profile,
                "--wait",
                "--timeout",
                "30m",
                "--output-format",
                "json",
            ],
            ["/usr/bin/xcrun", "stapler", "staple", str(app)],
            ["/usr/bin/xcrun", "stapler", "validate", str(app)],
            [
                "/usr/sbin/spctl",
                "--assess",
                "--type",
                "execute",
                "--verbose=4",
                str(app),
            ],
        ],
        "betweenStages": [
            "require-Apple-status-Accepted-and-review-log",
            "recompute-stapled-APP-tree-and-bundle-identities",
            "build-DMG-from-stapled-APP",
            "sign-DMG-with-explicit-Developer-ID-and-timestamp",
        ],
        "dmgStage": [
            [
                "/usr/bin/xcrun",
                "notarytool",
                "submit",
                str(dmg),
                "--keychain-profile",
                profile,
                "--wait",
                "--timeout",
                "30m",
                "--output-format",
                "json",
            ],
            ["/usr/bin/xcrun", "stapler", "staple", str(dmg)],
            ["/usr/bin/xcrun", "stapler", "validate", str(dmg)],
            [
                "/usr/sbin/spctl",
                "--assess",
                "--type",
                "open",
                "--context",
                "context:primary-signature",
                "--verbose=4",
                str(dmg),
            ],
        ],
        "final": [
            "record-final-DMG-hash-after-stapling",
            "offline-clean-machine-Gatekeeper-launch",
            "full-product-and-license-release-gates",
        ],
        "releaseReady": False,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", type=Path, required=True)
    p.add_argument("--dmg", type=Path, required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    out = new_output(a.output)
    record(out / "NOTARIZATION-PLAN.json", plan(a.app, a.dmg, profile=a.profile))
    print(json.dumps({"status": "plan-only", "executed": False, "releaseReady": False}))
