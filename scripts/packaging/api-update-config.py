"""Validate an in-place Compose edit using the effective configuration, without emitting secrets."""
from copy import deepcopy
from hashlib import file_digest
import json
from pathlib import Path
import re
import shlex
import sys


RULE_DIRECTORY = "PTCG_ONLINE_V2_RULE_DIRECTORY"
RULE_PIN = "PTCG_ONLINE_V2_RULE_MANIFEST_SHA256"
RULE_RELEASE = "PTCG_ONLINE_V2_APPROVED_RULE_RELEASE_ID"


def check_config(before, image, platform, old_rule, old_pin, rule, pin):
    services = before["services"]
    if not {"api", "migrate", "database"} <= services.keys():
        raise ValueError("Expected existing api, migrate and database services.")
    previous = services["api"]["image"]
    live_rule = services["api"]["environment"][RULE_RELEASE]
    if live_rule not in {old_rule, rule}:
        raise ValueError("The current rules are outside this update's supported upgrade path.")
    live_pin = old_pin if live_rule == old_rule else pin
    if (live_rule == rule) != (previous == image):
        raise ValueError("The configured API image and rule version disagree.")
    if "ptcg_online_server.migrate" not in services["migrate"].get("entrypoint", []):
        raise ValueError("Unrecognized migration command.")
    result = deepcopy(before)
    for name in ("api", "migrate"):
        existing = services[name]
        environment = existing["environment"]
        if (existing["image"] != previous or environment[RULE_RELEASE] != live_rule
                or environment[RULE_PIN] != live_pin or environment[RULE_DIRECTORY] != "/opt/ptcg-rules"
                or existing.get("platform", platform) != platform
                or existing.get("pull_policy", "missing") not in {"never", "missing"}
                or "build" in existing):
            raise ValueError("API and migration settings do not match the expected image-based deployment.")
        for volume in existing.get("volumes", []):
            target = Path(volume["target"] if isinstance(volume, dict) else volume.split(":")[1])
            rules = Path("/opt/ptcg-rules")
            if rules.is_relative_to(target) or target.is_relative_to(rules):
                raise ValueError("An existing volume shadows the new image's rules.")
        target = result["services"][name]
        target["image"] = image
        target["environment"].update({RULE_RELEASE: rule, RULE_PIN: pin})
    return result, previous


KIT_FILES = (
    "IMAGES.json", "README.md", "compose.base.json", "configure.py", "images/api.tar.gz",
    "images/caddy.tar.gz", "images/postgres.tar.gz", "images.env", "public_api.py", "server.sh",
)
KIT_REPLACED = {"IMAGES.json", "compose.base.json", "images/api.tar.gz", "images.env"}


def file_hash(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def read_json(path):
    try:
        return json.loads(path.read_bytes())
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path.name} at line {error.lineno}.") from None


def kit_update(previous, stage, update, old_rule, old_pin, actual_hashes):
    meta = read_json(previous / "IMAGES.json")
    manifest = read_json(previous / "MANIFEST.json")
    if (meta["schema"], meta["kind"], meta["platform"]) != (1, "ptcg-offline-api", update["platform"]):
        raise ValueError("Unexpected offline kit metadata.")
    if manifest["kind"] != meta["kind"] or manifest["ruleReleaseId"] != meta["ruleReleaseId"]:
        raise ValueError("The offline kit manifests disagree.")
    hashes = manifest["files"]
    if set(hashes) != set(KIT_FILES):
        raise ValueError("Unexpected offline kit inventory.")
    if hashes != actual_hashes:
        raise ValueError("Offline kit file hashes disagree with the verified inventory.")
    for name in ("IMAGES.json", "compose.base.json"):
        if file_hash(previous / name) != hashes[name]:
            raise ValueError(f"The staged copy of {name} changed.")
    checksums = {**hashes, "MANIFEST.json": file_hash(previous / "MANIFEST.json")}
    current = (previous / "SHA256SUMS").read_text()
    if sorted(current.splitlines()) != sorted(f"{value}  {name}" for name, value in checksums.items()):
        raise ValueError("The offline kit checksum list disagrees with its manifest.")

    rule = update["ruleReleaseId"]
    pin = update["ruleManifestSha256"]
    if meta["ruleReleaseId"] == rule:
        if meta["api"] != update["image"] or meta["ruleManifestSha256"] != pin:
            raise ValueError("The updated offline kit has a different image identity.")
        return
    if meta["ruleReleaseId"] != old_rule or meta["ruleManifestSha256"] != old_pin:
        raise ValueError("The offline kit is outside this update's supported upgrade path.")
    old_tag = meta["api"]["tag"]
    base = read_json(previous / "compose.base.json")
    base, configured_tag = check_config(
        base, update["image"]["tag"], update["platform"], old_rule, old_pin, rule, pin
    )
    if configured_tag != old_tag:
        raise ValueError("The offline kit's setup template pins a different image.")
    meta.update(api=update["image"], ruleReleaseId=rule, ruleManifestSha256=pin)
    manifest["images"].pop(old_tag)
    manifest["images"][meta["api"]["tag"]] = meta["api"]["id"]
    manifest.update(ruleReleaseId=rule, apiUpdateCommit=update["upstreamCommit"])
    stage.mkdir()

    def write_json(name, data):
        (stage / name).write_text(json.dumps(data, indent=2) + "\n")

    write_json("IMAGES.json", meta)
    write_json("compose.base.json", base)
    constants = {"PLATFORM": meta["platform"]}
    for name in ("api", "postgres", "caddy"):
        for suffix, key in (("IMAGE", "tag"), ("ID", "id"), ("MANIFEST_ID", "manifestId")):
            constants[f"{name.upper()}_{suffix}"] = meta[name][key]
    (stage / "images.env").write_text("".join(f"{k}={shlex.quote(v)}\n" for k, v in constants.items()))
    for name in KIT_REPLACED - {"images/api.tar.gz"}:
        hashes[name] = file_hash(stage / name)
    hashes["images/api.tar.gz"] = update["imageArchiveSha256"]
    write_json("MANIFEST.json", manifest)
    checksums = {**hashes, "MANIFEST.json": file_hash(stage / "MANIFEST.json")}
    (stage / "SHA256SUMS").write_text("".join(f"{checksums[n]}  {n}\n" for n in sorted(checksums)))

    deployment = previous / "DEPLOYMENT.json"
    if deployment.is_file():
        data = read_json(deployment)
        if data["ruleReleaseId"] not in {old_rule, rule} or data["platform"] != update["platform"]:
            raise ValueError("The configured deployment metadata pins a different release.")
        data["ruleReleaseId"] = rule
        write_json("DEPLOYMENT.json", data)


def main():
    operation, image, platform, old_rule, old_pin, rule, pin = sys.argv[1:8]
    work = Path("/work")
    try:
        if operation == "kit":
            previous = work / "kit-input"
            actual = dict(line.split("  ", 1)[::-1]
                          for line in (previous / "actual-hashes").read_text().splitlines())
            kit_update(previous, work / "kit-update",
                       read_json(work / "UPDATE.json"), old_rule, old_pin, actual)
            return
        before = json.loads((work / "before.json").read_bytes())
        expected, previous = check_config(before, image, platform, old_rule, old_pin, rule, pin)
        if operation == "prepare":
            original = (work / "original.yaml").read_bytes()
            if before == expected:
                sys.stdout.buffer.write(original)
                return
            substitutions = {previous: image, old_rule: rule, old_pin: pin}
            for old, new in substitutions.items():
                pattern = rb"(?<![\w./:-])" + re.escape(old.encode()) + rb"(?![\w./:-])"
                original, count = re.subn(pattern, new.encode(), original)
                if not count:
                    raise ValueError("The old values must be declared directly in the original Compose file.")
            sys.stdout.buffer.write(original)
        else:
            if json.loads((work / "after.json").read_bytes()) != expected:
                raise ValueError("The staged Compose file changes unrelated settings or omits an update value.")
    except (ValueError, KeyError, TypeError, OSError) as error:
        if isinstance(error, OSError) and error.filename:
            message = f"Cannot read or stage {Path(error.filename).name} ({error.strerror})."
        elif isinstance(error, KeyError):
            message = f"Missing update field {error}."
        else:
            message = str(error) if type(error) is ValueError else f"Invalid configuration ({type(error).__name__})."
        raise SystemExit(f"{operation}: {message}") from None


if __name__ == "__main__":
    main()
