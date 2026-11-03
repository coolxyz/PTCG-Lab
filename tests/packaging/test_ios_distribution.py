"""A Release handoff is a verified unsigned archive, never a simulated upload."""

import json
from pathlib import Path
import plistlib
import shutil
import zipfile

import pytest

from scripts.packaging import ios_distribution as distribution
from scripts.packaging.ios_inventory import supplement
from tests.packaging.test_ios_delivery import fixture, binary


@pytest.mark.parametrize("team,number", [("0000000000", "1"), ("TESTSIM000", "1"), ("short", "1"), ("AB12CD34EF", "0"), ("AB12CD34EF", "10000"), ("AB12CD34EF", "1; upload")])
def test_release_inputs_reject_placeholders_and_ambiguous_versions(team, number):
    with pytest.raises(ValueError):
        distribution.inputs(team, number)


def test_privacy_project_uses_resource_phase_only_on_ios_app():
    source = {"targets": {"app": {"type": "application", "platform": "iOS", "sources": []}, "library": {"type": "framework", "platform": "iOS"}}}
    result = distribution.project(source, team="AB12CD34EF")
    assert source["targets"]["app"]["sources"] == []
    assert result["targets"]["library"] == source["targets"]["library"]
    assert result == distribution.project(result, team="AB12CD34EF")
    assert result["targets"]["app"]["sources"] == [{"path": "../../PrivacyInfo.xcprivacy", "buildPhase": "resources"}]
    assert result["targets"]["app"]["settings"]["base"]["IPHONEOS_DEPLOYMENT_TARGET"] == "16.0"
    assert result["targets"]["app"]["deploymentTarget"] == result["options"]["deploymentTarget"]["iOS"] == "16.0"


def device_archive(tmp_path):
    app = fixture(tmp_path)
    (app / "Fixture").write_bytes(binary(platform=2))
    info = plistlib.loads((app / "Info.plist").read_bytes())
    info["CFBundleSupportedPlatforms"] = ["iPhoneOS"]
    (app / "Info.plist").write_bytes(plistlib.dumps(info))
    root = Path(__file__).resolve().parents[2]
    shutil.copyfile(root / distribution.PRIVACY, app / "PrivacyInfo.xcprivacy")
    inventory = app / "assets/licenses/INVENTORY.json"
    value = json.loads(inventory.read_bytes()); value["evidenceComplete"] = True
    inventory.write_text(json.dumps(value))
    archive = tmp_path / "app.xcarchive"
    (archive / "Products/Applications").mkdir(parents=True)
    app.rename(archive / "Products/Applications/Fixture.app")
    (archive / "Info.plist").write_bytes(plistlib.dumps({"ApplicationProperties": {"ApplicationPath": "Applications/Fixture.app"}}))
    output = tmp_path / "output"; output.mkdir()
    return archive, output


def test_archive_keeps_executable_permissions_and_records_unsigned_handoff(tmp_path):
    archive, output = device_archive(tmp_path)
    report = distribution.handoff(archive, output, team="AB12CD34EF", number="1", resources=None, source={"synthetic": True})
    assert not report["distributionSigned"] and not report["uploaded"] and not report["releaseReady"]
    assert report["status"] == "ready-for-operator-signing" and report["configuration"] == "Release"
    with zipfile.ZipFile(output / report["archive"]) as z:
        assert z.getinfo("app.xcarchive/Products/Applications/Fixture.app/Fixture").external_attr >> 16 & 0o111
    assert plistlib.loads((output / "ExportOptions.plist").read_bytes())["destination"] == "export"


def test_release_missing_privacy_or_incomplete_license_does_not_create_handoff(tmp_path):
    archive, output = device_archive(tmp_path)
    (archive / "Products/Applications/Fixture.app/PrivacyInfo.xcprivacy").unlink()
    with pytest.raises(FileNotFoundError):
        distribution.handoff(archive, output, team="AB12CD34EF", number="1", resources=None, source={})
    assert not list(output.iterdir())


def test_supplement_cannot_relabel_other_crate_or_vcs_commit(tmp_path):
    directory = tmp_path / "licenses/mobile"; directory.mkdir(parents=True)
    (directory / "supplemental.json").write_text(json.dumps({"schema": 1, "components": [{"name": "synthetic", "version": "1", "crateSha256": "wrong", "vcsCommit": "wrong", "pathInVcs": "wrong", "licenseDeclared": "MIT"}]}))
    class Archive:
        def extractfile(self, path):
            import io
            return io.BytesIO(json.dumps({"git": {"sha1": "actual"}, "path_in_vcs": "actual"}).encode())
    with pytest.raises(ValueError, match="identity"):
        supplement(tmp_path, {"name": "synthetic", "version": "1"}, {"checksum": "actual"}, {"license": "MIT"}, Archive())
