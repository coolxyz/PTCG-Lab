"""Build and verify a read-only DMG carrying a pinned C3-managed APP.

Uses no Applications symlink, root installer, policy bypass or auto-launch. The
user command delegates to the existing private per-user installation transaction.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.packaging.common import new_output, record, write, file_hash  # noqa: E402
from scripts.packaging.desktop_audit import audit  # noqa: E402
from scripts.packaging.source_identity import source_digest  # noqa: E402


def launcher(bundle_sha):
    import re

    if not re.fullmatch(r"[a-f0-9]{64}", bundle_sha):
        raise ValueError("Full approved bundle identity required")
    return (
        '''#!/bin/sh
set -eu
C4_MEDIA=$(CDPATH= cd -- "${0%/*}" && pwd -P)
C4_APP="$C4_MEDIA/PTCG Lab Preview.app"
exec "$C4_APP/Contents/Resources/backend/python/bin/python3" -I -B \\
  "$C4_APP/Contents/Resources/backend/app/scripts/product/desktop_install.py" \\
  --root "$HOME/Library/Application Support/org.ptcglab.desktop.preview.installations" \\
  --state-dir "$HOME/Library/Application Support/org.ptcglab.desktop.preview" \\
  install --candidate "$C4_APP" --sha256 "'''
        + bundle_sha
        + """" --adopt
"""
    )


def build(app, output, *, expected):
    if sys.platform != "darwin":
        raise ValueError("Native macOS required")
    from ptcg_desktop_api.installation import payload_record
    from scripts.product.desktop_install import bundle

    app = Path(app).absolute()
    info = bundle(app)
    if info["bundleSha256"] != expected:
        raise ValueError("APP bundle differs from approved input")
    payload_record(app / "Contents/Resources/backend")
    original = audit(app)
    if not original["signature"]["cryptographicVerification"]:
        raise ValueError("APP signature invalid")
    output = new_output(output)
    with tempfile.TemporaryDirectory(
        prefix="ptcg-c4-media-", dir=output.parent
    ) as temp:
        stage = Path(temp)
        shutil.copytree(app, stage / app.name)
        write(stage / "Install PTCG Lab.command", launcher(expected), mode=0o755)
        write(
            stage / "README.txt",
            "PTCG Lab macOS arm64 本机验收介质\n\n先关闭已有 PTCG Lab 窗口，再双击 Install PTCG Lab.command。无需管理员权限；它使用随包 Python，先备份再迁移当前 preview 用户数据。成功后终端输出 bundleSha256 和 transaction；当前 APP 位于用户 Library/Application Support/org.ptcglab.desktop.preview.installations/versions/<bundleSha256>/PTCG Lab Preview.app，双击该 APP 打开。请保留 transaction 以便按 C3 手顺成对回退。\n\n此介质仅用于本机验收，未完成 Developer ID／Apple 公证，不代表 Gatekeeper、最低系统或干净机通过。不要关闭系统保护；若系统不允许执行，请保留提示并联系发布方提供已签名公证版本。卸载应用不删除收藏、卡组或身份；不自动部署 API。\n",
            mode=0o644,
        )
        files = {
            p.relative_to(stage).as_posix(): file_hash(p)
            for p in stage.rglob("*")
            if p.is_file()
        }
        dmg = output / "PTCG-Lab-macOS-arm64-preview.dmg"
        subprocess.run(
            [
                "/usr/bin/hdiutil",
                "create",
                "-srcfolder",
                str(stage),
                "-volname",
                "PTCG Lab Preview",
                "-fs",
                "HFS+",
                "-format",
                "UDZO",
                str(dmg),
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
    subprocess.run(
        ["/usr/bin/hdiutil", "verify", str(dmg)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    if audit(app) != original:
        raise ValueError("Source APP changed")
    report = {
        "schema": 1,
        "stage": "P8-C4-DMG",
        "status": "candidate",
        "wrapperSourceSha256": source_digest(ROOT),
        "platform": "macos-arm64",
        "inputBundleSha256": expected,
        "inputAppTreeSha256": original["appTreeSha256"],
        "dmgSha256": file_hash(dmg),
        "dmgBytes": dmg.stat().st_size,
        "readOnlyFormat": "UDZO",
        "files": files,
        "signature": original["signature"],
        "notarization": "pending",
        "cleanMachineAccepted": False,
        "minimumOSAccepted": False,
        "releaseReady": False,
    }
    record(output / "DMG.json", report)
    return {k: v for k, v in report.items() if k not in {"files"}}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--bundle-sha256", required=True)
    a = p.parse_args()
    print(json.dumps(build(a.app, a.output, expected=a.bundle_sha256)))
