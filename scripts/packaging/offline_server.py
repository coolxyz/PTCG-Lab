"""Export a Docker-only, configurable API installer from a verified current-rule kit."""

import argparse
import json
from pathlib import Path
import shlex
import shutil
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.packaging import api_server, manage_server
from scripts.packaging.common import file_hash, manifest, new_output, record, run, write


def build(kit, output):
    kit = Path(kit).resolve()
    original = manage_server.verify(kit)
    if original.get("tlsMode") != "acme":
        raise ValueError("Use a current-rule ACME kit with API, PostgreSQL and Caddy archives")
    manage_server.images_match(original)
    api = next(tag for tag in original["images"] if tag.startswith("ptcg-lab-api-preview:"))
    pg = next(tag for tag in original["images"] if tag.startswith("ptcg-lab-postgres-preview:"))
    caddy = next(tag for tag in original["images"] if tag.startswith("ptcg-lab-caddy:"))
    api_server.verify_api_sources(api)
    # Check the actual bundled runtime, not just a copied manifest's declaration.
    code = (
        "from ptcg_engine_provider.bundle import DirectoryEngineProvider; "
        "from ptcg_core.factory import CoreFactory; "
        "print(CoreFactory(DirectoryEngineProvider('/opt/ptcg-rules',"
        + repr(original["ruleManifestSha256"]) + ").load()).runtime.release_id)"
    )
    release = run(["docker", "run", "--rm", "--network", "none", "--read-only",
                   "--tmpfs", "/tmp", "--entrypoint", "python", api, "-c", code], capture=True).strip()
    if release != original["ruleReleaseId"]:
        raise ValueError("Actual image rules differ from the kit")
    output = new_output(output)
    info = {
        "schema": 1, "kind": "ptcg-offline-api", "platform": original["platform"],
        "ruleReleaseId": release, "ruleManifestSha256": original["ruleManifestSha256"],
        **{name: {"tag": tag, "id": original["images"][tag]}
           for name, tag in (("api", api), ("postgres", pg), ("caddy", caddy))},
    }
    record(output / "IMAGES.json", info)
    constants = {"PLATFORM": info["platform"]}
    for name in ("api", "postgres", "caddy"):
        constants[name.upper() + "_IMAGE"] = info[name]["tag"]
        constants[name.upper() + "_ID"] = info[name]["id"]
    write(output / "images.env", "".join(key + "=" + shlex.quote(value) + "\n" for key, value in constants.items()))
    base = api_server.compose_document(api, pg, info["platform"], "https://unconfigured.invalid",
                                       "unconfigured", release, info["ruleManifestSha256"], 8876, "127.0.0.1")
    record(output / "compose.base.json", base)
    for source, target in (("offline-server.sh", "server.sh"), ("offline_setup.py", "configure.py"),
                           ("public_api.py", "public_api.py"), ("offline-server-README.md", "README.md")):
        shutil.copyfile(ROOT / "scripts/packaging" / source, output / target)
        (output / target).chmod(0o700 if target == "server.sh" else 0o600)
    (output / "images").mkdir()
    for name in ("api", "postgres", "caddy"):
        shutil.copyfile(kit / "images" / (name + ".tar.gz"), output / "images" / (name + ".tar.gz"))
    result = manifest(output, "ptcg-offline-api", platform=info["platform"],
                      ruleReleaseId=release, images=original["images"],
                      includesDeploymentSecrets=False, configuration="generated-on-server")
    write(output / "SHA256SUMS", "".join(
        file_hash(p) + "  " + p.relative_to(output).as_posix() + "\n"
        for p in sorted(output.rglob("*")) if p.is_file()
    ))
    archive = output.with_name(output.name + ".tar.gz")
    with tarfile.open(archive, "x:gz", compresslevel=5) as stream:
        stream.add(output, arcname=output.name)
    write(archive.with_name(archive.name + ".sha256"), file_hash(archive) + "  " + archive.name + "\n")
    return {"output": str(output), "archive": str(archive), "platform": result["platform"],
            "ruleReleaseId": release, "archiveSha256": file_hash(archive)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-kit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.from_kit, args.output)))
