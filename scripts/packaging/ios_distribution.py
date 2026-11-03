"""Release archive handoff. Signing, account access and upload are operator actions."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import plistlib
import re
import zipfile

from scripts.packaging.common import record, write
from scripts.packaging.ios_artifact import audit, IDENTIFIER

PRIVACY = "apps/mobile/src-tauri/PrivacyInfo.xcprivacy"


def inputs(team, number):
    if not isinstance(team, str) or not re.fullmatch(r"[A-Z0-9]{10}", team) or team in {"0000000000", "TESTSIM000"}:
        raise ValueError("Use a real ten-character Apple team ID")
    if not isinstance(number, str) or not re.fullmatch(r"[1-9][0-9]{0,3}", number):
        raise ValueError("Use an increasing build number between 1 and 9999")
    return {"developmentTeam": team, "bundleVersion": number}


def project(spec, *, team=None):
    spec = deepcopy(spec)
    spec.setdefault("options", {}).setdefault("deploymentTarget", {})["iOS"] = "16.0"
    found = False
    for target in spec["targets"].values():
        if target.get("type") != "application" or target.get("platform") != "iOS":
            continue
        found = True
        target["deploymentTarget"] = "16.0"
        target.setdefault("settings", {}).setdefault("base", {})["IPHONEOS_DEPLOYMENT_TARGET"] = "16.0"
        sources = target.setdefault("sources", [])
        entry = {"path": "../../PrivacyInfo.xcprivacy", "buildPhase": "resources"}
        if entry not in sources:
            sources.append(entry)
        if team:
            target.setdefault("settings", {}).setdefault("base", {})["DEVELOPMENT_TEAM"] = team
    if not found:
        raise ValueError("Generated iOS application target required")
    return spec


def validate_privacy(path):
    value = plistlib.loads(Path(path).read_bytes())
    if value.get("NSPrivacyTracking") is not False:
        raise ValueError("Current product does not implement cross-app tracking")
    reasons = {r["NSPrivacyAccessedAPIType"]: r["NSPrivacyAccessedAPITypeReasons"] for r in value["NSPrivacyAccessedAPITypes"]}
    if reasons.get("NSPrivacyAccessedAPICategoryFileTimestamp") != ["C617.1"] or reasons.get("NSPrivacyAccessedAPICategorySystemBootTime") != ["35F9.1"]:
        raise ValueError("App-container file metadata and elapsed-time reasons required")
    return value


def handoff(xcarchive, output, *, team, number, resources, source):
    inputs(team, number)
    xcarchive, output = Path(xcarchive), Path(output)
    info = plistlib.loads((xcarchive / "Info.plist").read_bytes())
    relative = info.get("ApplicationProperties", {}).get("ApplicationPath", "")
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Invalid archive application path")
    app = xcarchive / "Products" / relative
    report = audit(app, "device", expected_resources=resources)
    if report["buildNumber"] != number or report["identifier"] != IDENTIFIER:
        raise ValueError("Release archive identity or build number differs")
    validate_privacy(app / "PrivacyInfo.xcprivacy")
    inventory = json.loads((app / report["resources"]).parent.joinpath("licenses/INVENTORY.json").read_bytes())
    if not inventory.get("evidenceComplete"):
        raise ValueError("Release handoff requires complete dependency notice evidence")
    if any(p.is_symlink() for p in (xcarchive, *xcarchive.rglob("*"))):
        raise ValueError("Archive links forbidden")
    name = "PTCG-Lab-ios-release-unsigned.xcarchive.zip"
    path = output / name
    with zipfile.ZipFile(path, "x", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(xcarchive.rglob("*")):
            if not file.is_file():
                continue
            item = zipfile.ZipInfo(xcarchive.name + "/" + file.relative_to(xcarchive).as_posix())
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = (0o100755 if file.stat().st_mode & 0o111 else 0o100644) << 16
            archive.writestr(item, file.read_bytes())
    digest = sha256(path.read_bytes()).hexdigest()
    write(output / (name + ".sha256"), digest + "  " + name + "\n")
    options = {"method": "app-store-connect", "destination": "export", "teamID": team,
               "signingStyle": "automatic", "uploadSymbols": True, "manageAppVersionAndBuildNumber": False}
    (output / "ExportOptions.plist").write_bytes(plistlib.dumps(options))
    result = {"schema": 1, "kind": "ptcg-testflight-handoff", "configuration": "Release",
              "archive": name, "sha256": digest, "identifier": IDENTIFIER, "buildNumber": number,
              "source": source, "privacyManifestSha256": sha256((app / "PrivacyInfo.xcprivacy").read_bytes()).hexdigest(),
              "licenseEvidenceComplete": True, "distributionSigned": False, "uploaded": False,
              "status": "ready-for-operator-signing", "releaseReady": False}
    record(output / "TESTFLIGHT-HANDOFF.json", result)
    write(output / "TESTFLIGHT-HANDOFF.md", """# TestFlight Release 归档交接

本目录是实际 Release SDK 归档，尚未分发签名或上传。先核对 ZIP SHA-256，在全新目录用 ditto 解压并保留权限；在 Xcode Organizer 打开解压出的 .xcarchive。

使用对应真实 Apple 团队完成签名，Validate App 后选择 Distribute App → App Store Connect → Upload。计划外部测试时不要选 Internal Only。也可用 ExportOptions.plist 执行 xcodebuild -exportArchive，导出签名 IPA 后再上传；证书、profile 和账号由操作者管理，本工具没有访问账号、创建证书或上传。

上传前核对 Bundle ID、递增 Build、当前图标、隐私报告、依赖署名和加密问卷。当前含 TLS/AES-GCM，不能把未审阅的加密豁免结论硬编码为 NO。审核联系人、反馈邮箱、可访问的测试 API 和测试卡组由发布者填写。详见源码 docs/TestFlight发布.md。
""")
    return result
