"""Build a portable Windows preview from a verified, closed offline payload.

Requires native Windows SDK/Rust. Never substitutes an untested cross-built EXE.
No installation, registry policy, server deployment or user identity is modified.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.native.windows_payload_check import check, verify  # noqa: E402
from scripts.packaging.common import manifest, new_output, record, run, write  # noqa: E402


def build(payload, output, *, offline=False):
    if (
        sys.platform != "win32"
        or os.name != "nt"
        or platform.machine().lower() not in {"amd64", "x86_64"}
    ):
        raise ValueError("Build the Windows EXE with native Windows x64 and MSVC SDK")
    payload = verify(payload)
    if not run(["rustc", "--version"], capture=True).startswith("rustc 1.90.0 "):
        raise ValueError("Use Rust 1.90.0")
    proof = check(payload)
    scratch_root = ROOT / "var/release-build"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="windows-x64-", dir=scratch_root) as temp:
        scratch = Path(temp)
        env = {**os.environ, "CARGO_TARGET_DIR": str(scratch / "target")}
        env.pop("STATIC_VCRUNTIME", None)
        command = [
            "cargo",
            "build",
            "--locked",
            "--release",
            "--target",
            "x86_64-pc-windows-msvc",
            "--manifest-path",
            ROOT / "apps/native-shell/src-tauri/Cargo.toml",
        ]
        if offline:
            command.append("--offline")
        run(command, env=env)
        owner = [
            "cargo",
            "run",
            "--locked",
            "--release",
            "--no-default-features",
            "--example",
            "backend_probe",
            "--target",
            "x86_64-pc-windows-msvc",
            "--manifest-path",
            ROOT / "apps/native-shell/src-tauri/Cargo.toml",
        ]
        if offline:
            owner.append("--offline")
        owner += ["--", payload, scratch / "new-private-profile"]
        owner_proof = json.loads(run(owner, env=env, capture=True))
        if len(owner_proof) != 5 or not all(owner_proof.values()):
            raise ValueError("Native Rust owner probe incomplete")
        exe = scratch / "target/x86_64-pc-windows-msvc/release/ptcg-native-shell.exe"
        from scripts.native.windows_payload import pe_machine

        pe_machine(exe)
        output = new_output(output)
        shutil.copy2(exe, output / "PTCG Lab Preview.exe")
        shutil.copytree(payload, output / "backend")
        record(output / "BACKEND-PROBE.json", proof)
        record(output / "BACKEND-OWNER-PROBE.json", owner_proof)
        for name, module in [
            ("configure-server.cmd", "configure"),
            ("identity.cmd", "identity"),
        ]:
            write(
                output / name,
                '@echo off\r\n"%~dp0backend\\python\\python.exe" -I -B -X utf8 -m ptcg_desktop_api.'
                + module
                + " %*\r\n",
            )
        write(
            output / "README.md",
            """# Windows x64 人工验收预览包

从 ZIP 完整解压后双击 `PTCG Lab Preview.exe`；运行时、依赖和当前规则已在 backend 中，用户无需 Python、Node、Rust 或 WSL2。需要 Microsoft Edge WebView2 Runtime；缺少时按官方安装流程准备，不关闭系统保护。当前是未签名的 preview，最低系统与非管理员用户矩阵仍待实机验收。

默认离线。卡组、收藏、练习和本机凭据位于 LocalAppData 的独立 preview 身份目录；关闭 APP 回收自有后台进程树。不要直接复制 DPAPI 密文当作迁移。

关闭 APP 后在终端执行 `configure-server.cmd --client-kit "完整 API kit 路径"` 配置同批服务，已有配置会拒绝覆盖，不创建或导入身份。身份交接使用 `identity.cmd import --bundle "加密文件路径" --state-dir "preview 状态目录" --profile preview`，口令只在终端隐藏输入。应先停止原客户端、备份原身份；迁移保持原服务器与待确认请求，不允许跨规则恢复或改写身份。

BACKEND-PROBE 只证明随包后台和五套 H2 路由，BACKEND-OWNER-PROBE 证明 Rust 生命周期；交互式 WebView2、完整自然对局、签名与安装升级仍需单独验收。此包不部署、重启或扫描任何 LAN 服务。
""",
        )
        archive = Path(
            shutil.make_archive(
                str(scratch / "windows-preview"), "zip", root_dir=output, base_dir="."
            )
        )
        shutil.copy2(archive, output / "PTCG-Lab-Windows-x64-preview.zip")
        return manifest(
            output,
            "ptcg-windows-preview",
            platform="windows-x64",
            signedRelease=False,
            python="3.14.10",
            declaredMinimumWindows="10",
            minimumWindowsActuallyTested=False,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "release/windows-x64")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    result = build(args.payload, args.output, offline=args.offline)
    print(json.dumps({"status": result["status"], "releaseReady": False}))
