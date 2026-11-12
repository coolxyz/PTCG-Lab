"""Wrap a verified Windows x64 bundle with the NSIS installer and offline rule update."""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.packaging.common import file_hash, new_output, record, write
from scripts.packaging.local_rules import media_inputs, pinned, regular


def launcher(archive_pin, payload_pin):
    return f'''@echo off
setlocal
chcp 65001 >nul
set "BASE=%~dp0"
"%BASE%backend\\python\\python.exe" -I -B -X utf8 "%BASE%Local Rules\\local_rules.py" apply ^
  --archive "%BASE%Local Rules\\update.zip" --expected "{pinned(archive_pin)}" ^
  --payload "%BASE%backend" --payload-pin "{pinned(payload_pin)}" ^
  --profile "%LOCALAPPDATA%\\org.ptcglab.desktop.preview"
set "RESULT=%ERRORLEVEL%"
if /I not "%~1"=="--no-pause" pause
exit /b %RESULT%
'''


def inventory(root):
    return {
        p.relative_to(root).as_posix(): file_hash(regular(root, p.relative_to(root).as_posix()))
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def uninstall_files(root, output):
    directories = sorted(p.relative_to(root).parts for p in root.rglob("*") if p.is_dir())
    files = sorted(
        p.relative_to(root).parts for p in root.rglob("*")
        if p.is_file() and p.name != "ptcg-setup.marker"
    )

    def nsis_path(parts):
        if any(any(c in part for c in '$"\\:\r\n') for part in parts):
            raise ValueError("Unsafe NSIS path")
        return "${base}\\" + "\\".join(parts)

    lines = ["!macro CheckProgramDirectories base"]
    lines += [f'  !insertmacro CheckDirectory "{nsis_path(p)}"' for p in directories]
    lines += ["!macroend", "", "!macro RemoveProgramFiles base", "  ClearErrors"]
    lines += [f'  Delete "{nsis_path(p)}"' for p in files]
    lines += [f'  RMDir "{nsis_path(p)}"' for p in reversed(directories)]
    lines += ["!macroend", ""]
    write(output, "\n".join(lines))


def build(bundle, local_rules, makensis, output):
    bundle, local_rules, makensis = map(lambda p: Path(p).resolve(), (bundle, local_rules, makensis))
    expected = json.loads((bundle / "FILES.json").read_bytes())
    actual = inventory(bundle)
    actual.pop("FILES.json")
    if expected != {"schema": 1, "files": actual}:
        raise ValueError("Windows bundle inventory differs from the approved input.")
    rules = media_inputs(local_rules, bundle / "backend")
    payload_pin = file_hash(bundle / "backend/manifest.json")
    version = json.loads((ROOT / "RELEASE.json").read_bytes())["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-.][a-zA-Z0-9.-]+)?", version):
        raise ValueError("Invalid Windows setup version")
    output = new_output(output)
    portable = output / "bundle"
    shutil.copytree(bundle, portable)
    updates = portable / "Local Rules"
    updates.mkdir()
    shutil.copy2(local_rules, updates / "update.zip")
    shutil.copy2(ROOT / "scripts/packaging/local_rules.py", updates / "local_rules.py")
    (portable / "Update Local Rules.cmd").write_bytes(
        launcher(rules["archiveSha256"], payload_pin).replace("\n", "\r\n").encode("ascii")
    )
    files = inventory(portable)
    files.pop("FILES.json")
    (portable / "FILES.json").unlink()
    record(portable / "FILES.json", {"schema": 1, "files": files})
    archive = Path(shutil.make_archive(str(output / "PTCG-Lab-Windows-x64"), "zip", root_dir=portable))
    setup = output / "PTCG-Lab-Windows-x64-Setup.exe"
    with TemporaryDirectory(prefix="ptcg-nsis-", dir=output.parent) as temporary:
        scratch = Path(temporary)
        app_files = scratch / "app-files"
        app_files.mkdir()
        for name in ("PTCG Lab Preview.exe", "configure-server.cmd", "identity.cmd", "Update Local Rules.cmd"):
            shutil.copy2(portable / name, app_files / name)
        for name in ("backend", "Local Rules"):
            shutil.copytree(portable / name, app_files / name)
        write(app_files / "ptcg-setup.marker", "org.ptcglab.desktop.preview\n")
        uninstall = scratch / "files.nsh"
        uninstall_files(app_files, uninstall)
        command = [
            str(makensis), f"-DSETUP_OUTPUT={setup}",
            f"-DSETUP_ICON={ROOT / 'apps/native-shell/src-tauri/icons/icon.ico'}",
            f"-DAPP_FILES={app_files}", f"-DUNINSTALL_FILES={uninstall}",
            f"-DSETUP_VERSION={version}", str(ROOT / "scripts/packaging/windows_setup.nsi"),
        ]
        subprocess.run(command, check=True, timeout=300)
        installed = inventory(app_files)
    report = {
        "schema": 1, "platform": "windows-x64", "setupSha256": file_hash(setup),
        "zipSha256": file_hash(archive), "payloadManifestSha256": payload_pin,
        "localRules": rules, "installedFiles": installed, "nativeExecution": "pending",
    }
    record(output / "WINDOWS-SETUP.json", report)
    return {key: value for key, value in report.items() if key != "installedFiles"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "local-rules", "makensis", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    print(json.dumps(build(**vars(parser.parse_args()))))
