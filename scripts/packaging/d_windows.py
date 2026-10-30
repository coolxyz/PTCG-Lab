"""Build an unsigned Windows portable candidate with cargo-xwin on macOS.

Cross compilation is deliberately separate from native B4 acceptance. No target
program is executed here, no Windows SDK is redistributed, no service is touched.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.packaging.common import file_hash, new_output, record, run, write  # noqa: E402
from scripts.packaging.source_identity import source_digest  # noqa: E402
from scripts.native.windows_payload_check import verify  # noqa: E402


def pe_gui(path):
    data = Path(path).read_bytes()
    if len(data) < 256 or data[:2] != b"MZ":
        raise ValueError("Missing PE header")
    offset = struct.unpack_from("<I", data, 60)[0]
    if offset + 96 > len(data) or data[offset : offset + 4] != b"PE\0\0":
        raise ValueError("Invalid PE offset")
    machine = struct.unpack_from("<H", data, offset + 4)[0]
    magic = struct.unpack_from("<H", data, offset + 24)[0]
    subsystem = struct.unpack_from("<H", data, offset + 24 + 68)[0]
    if (machine, magic, subsystem) != (0x8664, 0x20B, 2):
        raise ValueError("Require actual AMD64 PE32+ Windows GUI executable")
    return {"machine": "AMD64", "format": "PE32+", "subsystem": "WINDOWS_GUI"}


VERIFY = r"""$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$m = Get-Content -LiteralPath (Join-Path $root 'FILES.json') -Raw | ConvertFrom-Json
$actual = @(Get-ChildItem -LiteralPath $root -File -Recurse | ForEach-Object {
  $_.FullName.Substring($root.Length + 1).Replace('\','/')
} | Where-Object { $_ -ne 'FILES.json' } | Sort-Object)
$expected = @($m.files.PSObject.Properties.Name | Sort-Object)
if (Compare-Object $actual $expected) { throw 'Unexpected or missing package file' }
foreach ($p in $m.files.PSObject.Properties) {
  $f = Join-Path $root $p.Name
  if ((Get-Item -LiteralPath $f).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point refused' }
  if ((Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLowerInvariant() -ne $p.Value) { throw 'Package hash mismatch' }
}
Write-Host 'Exact package inventory and SHA256 verified.'
& (Join-Path $root 'backend/python/python.exe') -I -B -X utf8 (Join-Path $root 'backend-probe.py') (Join-Path $root 'backend') --all-strategies
if ($LASTEXITCODE -ne 0) { throw 'Bundled backend probe failed' }
Write-Host 'Backend probe passed. Native window, DPAPI, Job Object and full games still require the documented checks.'
"""


def build(
    payload, output, *, rust_bin, cargo_home, llvm_env, cargo_xwin, cache, target
):
    if sys.platform != "darwin":
        raise ValueError("Use the native Windows builder on Windows")
    payload = verify(payload)
    tool = json.loads(Path(llvm_env).read_text())
    env = {
        **os.environ,
        "PATH": ":".join(
            [
                tool["bin"],
                tool["lld"],
                str(rust_bin),
                str(Path(cargo_xwin).parent),
                "/usr/bin",
                "/bin",
            ]
        ),
        "DYLD_LIBRARY_PATH": tool["DYLD_LIBRARY_PATH"],
        "CARGO_HOME": str(cargo_home),
        "RUSTC": str(Path(rust_bin) / "rustc"),
        "RUSTDOC": str(Path(rust_bin) / "rustdoc"),
        "XWIN_CACHE_DIR": str(cache),
        "CARGO_TARGET_DIR": str(target),
        "CARGO_BUILD_JOBS": "1",
    }
    for key in [
        "STATIC_VCRUNTIME",
        "CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_RUSTFLAGS",
        "CARGO_ENCODED_RUSTFLAGS",
        "RUSTFLAGS",
    ]:
        env.pop(key, None)
    rust = run([Path(rust_bin) / "rustc", "--version"], env=env, capture=True).strip()
    xwin = run([cargo_xwin, "--version"], env=env, capture=True).strip()
    if not rust.startswith("rustc 1.90.0 ") or xwin != "cargo-xwin 0.23.1":
        raise ValueError("Use pinned Rust 1.90.0 and cargo-xwin 0.23.1")
    before = source_digest(ROOT)
    run(
        [
            cargo_xwin,
            "build",
            "--locked",
            "--release",
            "--target",
            "x86_64-pc-windows-msvc",
            "--manifest-path",
            ROOT / "apps/native-shell/src-tauri/Cargo.toml",
            "--jobs",
            "1",
        ],
        env=env,
    )
    exe = Path(target) / "x86_64-pc-windows-msvc/release/ptcg-native-shell.exe"
    proof = pe_gui(exe)
    imports = run(
        [Path(tool["bin"]) / "llvm-readobj", "--coff-imports", exe],
        env=env,
        capture=True,
    )
    dlls = sorted(
        {
            line.strip().split(": ", 1)[1]
            for line in imports.splitlines()
            if line.strip().startswith("Name: ")
        }
    )
    if not dlls or any(d.lower().startswith(("vcruntime", "msvcp")) for d in dlls):
        raise ValueError("Unexpected external Visual C++ runtime dependency")
    if before != source_digest(ROOT):
        raise ValueError("Sources changed during cross compilation")
    output = new_output(output)
    shutil.copy2(exe, output / "PTCG Lab Preview.exe")
    shutil.copytree(payload, output / "backend")
    shutil.copy2(ROOT / "scripts/native/probe.py", output / "backend-probe.py")
    shutil.copy2(
        ROOT / "docs/Windows验证步骤.md", output / "Windows验收步骤.md"
    )
    shutil.copy2(ROOT / "docs/启动使用与跨系统迁移.md", output / "使用说明.md")
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
    write(output / "Verify.ps1", VERIFY)
    record(
        output / "BUILD.json",
        {
            "schema": 1,
            "stage": "P8-D",
            "status": "cross-compiled-candidate",
            "sourceSha256": before,
            "rust": rust,
            "cargoXwin": xwin,
            "pe": proof,
            "imports": dlls,
            "executableSha256": file_hash(exe),
            "nativeExecution": "pending",
            "nativeWindowsAccepted": False,
            "signed": False,
            "releaseReady": False,
        },
    )
    record(
        output / "FILES.json",
        {
            "schema": 1,
            "files": {
                p.relative_to(output).as_posix(): file_hash(p)
                for p in sorted(output.rglob("*"))
                if p.is_file()
            },
        },
    )
    archive = Path(
        shutil.make_archive(
            str(output.parent / "PTCG-Lab-Windows-x64-P8D-preview"),
            "zip",
            root_dir=output,
        )
    )
    return {
        "schema": 1,
        "status": "cross-compiled-candidate",
        "sourceSha256": before,
        "exeSha256": file_hash(exe),
        "zipSha256": file_hash(archive),
        "zipBytes": archive.stat().st_size,
        "pe": proof,
        "imports": dlls,
        "nativeExecution": "pending",
        "releaseReady": False,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in [
        "payload",
        "output",
        "rust-bin",
        "cargo-home",
        "llvm-env",
        "cargo-xwin",
        "cache",
        "target",
    ]:
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    result = build(
        a.payload,
        a.output,
        rust_bin=a.rust_bin.absolute(),
        cargo_home=a.cargo_home.absolute(),
        llvm_env=a.llvm_env,
        cargo_xwin=a.cargo_xwin.absolute(),
        cache=a.cache.absolute(),
        target=a.target.absolute(),
    )
    record(a.output.parent / "WINDOWS-ARTIFACT.json", result)
    print(json.dumps(result))
