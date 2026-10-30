"""Build a relocatable macOS arm64 preview APP and ZIP; never copy user profiles."""

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
from scripts.native.assemble import assemble  # noqa: E402
from scripts.native.probe import probe  # noqa: E402
from scripts.packaging.common import manifest, new_output, record, run, write  # noqa: E402


def build(
    runtime,
    assets,
    output,
    *,
    cargo_home=None,
    rust_bin=None,
    target_dir=None,
    build_mode="debug",
    parser_wheel=None,
):
    if parser_wheel is None:
        raise ValueError("Supply the pinned pure Python parser wheel")
    if build_mode not in {"debug", "release"}:
        raise ValueError("Choose debug or release")
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ValueError("This preview builder requires macOS arm64")
    lock = json.loads((ROOT / "scripts/native/runtime-lock.json").read_text())
    env = {
        **os.environ,
        "CARGO_BUILD_JOBS": "1",
        "MACOSX_DEPLOYMENT_TARGET": lock["minimumMacOS"],
        # LLVM symbol stripping emits misaligned LINKEDIT pools on the current
        # Apple linker. Keep release optimizations and retain symbols explicitly.
        "CARGO_PROFILE_RELEASE_STRIP": "none",
    }
    if rust_bin:
        env["PATH"] = str(Path(rust_bin).resolve()) + os.pathsep + env["PATH"]
    if rust_bin:
        env["RUSTC"] = str(Path(rust_bin).resolve() / "rustc")
        env["RUSTDOC"] = str(Path(rust_bin).resolve() / "rustdoc")
    if cargo_home:
        env["CARGO_HOME"] = str(Path(cargo_home).resolve())
    if not run(["rustc", "--version"], env=env, capture=True).startswith(
        "rustc " + lock["rust"] + " "
    ):
        raise ValueError("Install the Rust version pinned in runtime-lock.json")
    output = new_output(output)
    build_home = ROOT / "var/release-build"
    build_home.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=output.name + "-", dir=build_home))
    env["CARGO_TARGET_DIR"] = str(
        Path(target_dir).resolve() if target_dir else scratch / "target"
    )
    run(["npm", "run", "product:build"], env=env)
    payload = scratch / "payload"
    assemble(runtime, assets, payload)
    from scripts.packaging.prepare_macos import prepare

    record(output / "PACKAGING-REPAIRS.json", prepare(payload, parser_wheel, root=ROOT))
    from scripts.packaging.runtime_trim import trim

    record(output / "RUNTIME-CLOSURE.json", trim(payload, root=ROOT))
    record(output / "BACKEND-PROBE.json", probe(payload))
    from scripts.packaging.desktop_inventory import generate
    from scripts.packaging.desktop_audit import audit

    distribution = scratch / "distribution"
    inventory = generate(
        payload,
        distribution,
        root=ROOT,
        cargo_home=env.get("CARGO_HOME", Path.home() / ".cargo"),
    )
    from scripts.packaging.source_identity import source_digest

    record(
        distribution / "BUILD-SOURCE.json",
        {
            "schema": 1,
            "packagingSourceSha256": source_digest(ROOT),
            "buildMode": build_mode,
            "releaseStrip": "none",
            "rulePolicy": "current-only-v1",
        },
    )
    config = scratch / "tauri-payload.json"
    record(
        config,
        {
            "bundle": {
                "resources": {
                    str(payload) + "/": "backend/",
                    str(distribution) + "/": "distribution/",
                }
            }
        },
    )
    run(
        [
            "npm",
            "run",
            "tauri",
            "--workspace",
            "@ptcg/native-shell",
            "--",
            "build",
            *(["--debug"] if build_mode == "debug" else []),
            "--bundles",
            "app",
            "--config",
            config,
        ],
        env=env,
    )
    name = "PTCG Lab Preview.app"
    source = Path(env["CARGO_TARGET_DIR"]) / build_mode / "bundle/macos" / name
    shutil.copytree(source, output / name)
    # Seal the complete APP resource envelope using a local ad-hoc signature.
    # Do not --deep re-sign Python libraries: their payload hashes are sealed.
    # This never selects a keychain identity, hardened runtime or notarization.
    run(["/usr/bin/codesign", "--force", "--sign", "-", output / name])
    run(["/usr/bin/codesign", "--verify", "--deep", "--strict", output / name])
    record(
        output / "ARTIFACT-AUDIT.json",
        audit(output / name, minimum=lock["minimumMacOS"], root=ROOT),
    )
    record(
        output / "COMPONENT-SUMMARY.json",
        {
            "components": len(inventory["components"]),
            "licenseEvidenceFiles": len(inventory["licenseFiles"]),
            "licenseReview": "pending",
            "releaseReady": False,
        },
    )
    # ditto preserves the APP layout and executable bits; no signing claims.
    run(
        [
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            output / name,
            output / "PTCG-Lab-macOS-arm64-preview.zip",
        ]
    )
    write(
        output / "README.md",
        """# macOS arm64 人工验收预览包

解压 ZIP 后将 `PTCG Lab Preview.app` 复制到自己的应用目录，双击启动。
用户无需安装 Python、Node 或 Rust。当前为未公证的预览包，编译模式见 MANIFEST.json；release 编译不等于签名发行；若系统拦截，按 macOS 的“隐私与安全性”流程确认来源，不关闭系统保护。

产品收藏、卡组和练习数据保存在 preview 独立 Application Support 目录；关闭 APP 会关闭自有后台。默认离线，可先测试资料、构筑与人机练习。
网络测试请先启动同批 API Server，再在其目录运行 `configure-mac.command`；配置过程不导入已有游客身份，不覆盖既有服务器配置。

此包仅 macOS arm64，声明编译目标为 macOS 12.0，最低系统实机、Intel、Windows、签名／公证、完整更新链路和完整自然对局尚待后续验收。
BACKEND-PROBE.json 只记录随包后台探针；不代表人工 WebView 验收通过。
APP 的 Contents/Resources/distribution 中携带组件清单、SPDX SBOM 与许可证据。Cargo 列表含构建及其他目标闭包，运行时复合依赖、许可审核及干净机仍待完成。
运行构建的日志和 payload 在构建仓库的私有 var/release-build 目录，不在 APP 内存放用户数据库、凭据或研究数据。
""",
    )
    return manifest(
        output,
        "ptcg-macos-preview",
        platform="macos-arm64",
        signedRelease=False,
        adHocResourceSeal=True,
        buildMode=build_mode,
        releaseStrip="none",
        componentInventory="Contents/Resources/distribution/COMPONENTS.json",
        sbom="Contents/Resources/distribution/SBOM.spdx.json",
        minimumMacOSActuallyTested=False,
        python=lock["python"],
        declaredMinimumMacOS=lock["minimumMacOS"],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parser-wheel", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "release/macos-arm64")
    parser.add_argument("--cargo-home", type=Path)
    parser.add_argument("--rust-bin", type=Path)
    parser.add_argument("--target-dir", type=Path)
    parser.add_argument("--build-mode", choices=["debug", "release"], default="debug")
    args = parser.parse_args()
    report = build(
        args.runtime,
        args.assets,
        args.output,
        cargo_home=args.cargo_home,
        rust_bin=args.rust_bin,
        target_dir=args.target_dir,
        build_mode=args.build_mode,
        parser_wheel=args.parser_wheel,
    )
    print(json.dumps({"status": report["status"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
