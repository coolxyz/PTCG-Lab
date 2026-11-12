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
from scripts.packaging.local_rules import media_inputs  # noqa: E402
from scripts.packaging.source_identity import source_digest  # noqa: E402


def launcher(bundle_sha, *, rules=None, payload_pin=None):
    import re

    if not re.fullmatch(r"[a-f0-9]{64}", bundle_sha):
        raise ValueError("Full approved bundle identity required")
    if rules:
        from scripts.packaging.local_rules import pinned

        return f'''#!/bin/sh
set -eu
C4_MEDIA=$(CDPATH= cd -- "${{0%/*}}" && pwd -P)
C4_APP="$C4_MEDIA/PTCG Lab Preview.app"
C4_PAYLOAD="$C4_APP/Contents/Resources/backend"
C4_PROFILE="$HOME/Library/Application Support/org.ptcglab.desktop.preview"
C4_INSTALL="$HOME/Library/Application Support/org.ptcglab.desktop.preview.installations"
if [ "$#" -eq 4 ] && [ "$1" = "--profile" ] && [ "$3" = "--install-root" ]; then
  C4_PROFILE="$2"
  C4_INSTALL="$4"
elif [ "$#" -ne 0 ]; then
  echo "Usage: Install PTCG Lab.command [--profile PATH --install-root PATH]" >&2
  exit 2
fi
exec "$C4_PAYLOAD/python/bin/python3" -I -B "$C4_MEDIA/Local Rules/local_rules.py" apply \\
  --archive "$C4_MEDIA/Local Rules/update.zip" --expected "{pinned(rules['archiveSha256'])}" \\
  --app "$C4_APP" --bundle-pin "{bundle_sha}" \\
  --payload "$C4_PAYLOAD" --payload-pin "{pinned(payload_pin)}" \\
  --install-root "$C4_INSTALL" --profile "$C4_PROFILE"
'''
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


def build(app, output, *, expected, local_rules=None):
    if sys.platform != "darwin":
        raise ValueError("Native macOS required")
    from ptcg_desktop_api.installation import payload_record
    from scripts.product.desktop_install import bundle

    app = Path(app).absolute()
    info = bundle(app)
    if info["bundleSha256"] != expected:
        raise ValueError("APP bundle differs from approved input")
    payload = app / "Contents/Resources/backend"
    payload_pin = payload_record(payload)["payloadSha256"]
    rules = media_inputs(local_rules, payload) if local_rules else None
    original = audit(app)
    if not original["signature"]["cryptographicVerification"]:
        raise ValueError("APP signature invalid")
    output = new_output(output)
    with tempfile.TemporaryDirectory(
        prefix="ptcg-c4-media-", dir=output.parent
    ) as temp:
        stage = Path(temp)
        shutil.copytree(app, stage / app.name)
        if rules:
            updates = stage / "Local Rules"
            updates.mkdir()
            shutil.copy2(local_rules, updates / "update.zip")
            shutil.copy2(ROOT / "scripts/packaging/local_rules.py", updates / "local_rules.py")
        write(stage / "Install PTCG Lab.command", launcher(expected, rules=rules, payload_pin=payload_pin), mode=0o755)
        write(
            stage / "README.txt",
            "PTCG Lab macOS arm64 本机验收介质\n\n先关闭已有 PTCG Lab 窗口，再双击 Install PTCG Lab.command。无需管理员权限；它使用随包 Python，先备份再迁移当前 preview 用户数据。成功后终端输出 bundleSha256 和 transaction；当前 APP 位于用户 Library/Application Support/org.ptcglab.desktop.preview.installations/versions/<bundleSha256>/PTCG Lab Preview.app，双击该 APP 打开。请保留 transaction 以便按 C3 手顺成对回退。\n\n此介质仅用于本机验收，未完成 Developer ID／Apple 公证，不代表 Gatekeeper、最低系统或干净机通过。不要关闭系统保护；若系统不允许执行，请保留提示并联系发布方提供已签名公证版本。卸载应用不删除收藏、卡组或身份；不自动部署 API。\n"
            + ("\n该入口也会启用本批批准的本机规则。若规则升级失败，退出 App 后用同一入口重试。\n" if rules else ""),
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
        "localRules": rules,
        "releaseReady": False,
    }
    record(output / "DMG.json", report)
    return {k: v for k, v in report.items() if k not in {"files"}}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--bundle-sha256", required=True)
    p.add_argument("--local-rules", type=Path)
    a = p.parse_args()
    print(json.dumps(build(a.app, a.output, expected=a.bundle_sha256, local_rules=a.local_rules)))
