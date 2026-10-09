"""Create full regression evidence; capture inputs before running any checks."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.sync.common import write, now


def main():
    output = ROOT / "artifacts/sync"
    output.mkdir(parents=True, exist_ok=True)
    files = {}
    for folder in ("packages", "apps/api", "apps/web/src", "apps/web/e2e", "tests", "scripts/sync", "scripts/simulation", "rulesets", "data/sync"):
        for p in (ROOT / folder).rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".tsbuildinfo"):
                files[p.relative_to(ROOT).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    for name in ("data/catalog/catalog.json", "data/catalog/card-details.json", "data/simulation/effects.json", "data/cardpool/plain-pokemon.json", "data/cardpool/battle-scope.json", "data/cardpool/source-exceptions.json", "apps/web/package-lock.json", "apps/web/playwright.config.ts", "artifacts/engine/overlay-hashes.json"):
        files[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    p3 = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests/simulation").glob("test_*.py"))
    expensive = [p for p in p3 if Path(p).name in ("test_a2.py", "test_a2_service.py", "test_information.py")]
    groups = [["tests/catalog", "tests/rules", "tests/collection", "tests/battle", "tests/portable", "tests/sync"], expensive, [p for p in p3 if p not in expensive], ["tests/cardpool"]]
    matrix = "tests/simulation/test_prompt_matrix.py"
    groups[2].remove(matrix)
    shard_start = len(groups)
    shard_count = 4
    groups.extend([[matrix] for _ in range(shard_count)])
    write(output / "regression-input.json", {"createdAt": now(), "files": files, "testPartitions": groups})
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(ROOT / p) for p in (".", "tests/rules", "runtime/engine"))}
    scratch = ROOT / "var" / ("sync-regression-" + uuid.uuid4().hex)
    scratch.mkdir(parents=True)
    processes = []
    try:
        for i, group in enumerate(groups):
            log = (output / f"backend-{i}.log").open("wb")
            command = [sys.executable, "-X", "utf8", "-m", "pytest", *group, "--import-mode=importlib", "-q", "--basetemp=" + str(scratch / str(i)), "-p", "no:cacheprovider", f"--junitxml=artifacts/sync/backend-{i}.xml"]
            partition_env = env.copy()
            if i >= shard_start:
                command += ["-p", "scripts.sync.pytest_shard"]
                partition_env.update(PTCG_TEST_SHARDS=str(shard_count), PTCG_TEST_SHARD=str(i-shard_start),
                                     PTCG_TEST_SHARD_REPORT=str(output / f"shard-{i}.json"))
            processes.append((subprocess.Popen(command, cwd=ROOT, env=partition_env, stdout=log, stderr=subprocess.STDOUT), log))
        codes = [process.wait() for process, _ in processes]
    finally:
        for process, log in processes:
            if process.poll() is None:
                process.terminate()
                process.wait()
            log.close()
    # Fail closed if collection differs, a case is missing, or any case ran twice.
    coverage = [json.loads((output / f"shard-{i}.json").read_text(encoding="utf-8"))
                for i in range(shard_start, len(groups))]
    expected = coverage[0]["all"]
    actual = [node for shard in coverage for node in shard["selected"]]
    assert all(shard["all"] == expected for shard in coverage)
    assert len(actual) == len(set(actual)) == len(expected) and set(actual) == set(expected)
    suites = ET.Element("testsuites")
    for i in range(len(groups)):
        suites.extend(list(ET.parse(output / f"backend-{i}.xml").getroot()))
    ET.ElementTree(suites).write(output / "backend.xml", encoding="utf-8", xml_declaration=True)
    if any(codes):
        raise SystemExit(1)
    commands = [([shutil.which("npm") or "npm", "run", "build"], ROOT / "apps/web", "build.log"),
                ([shutil.which("npx") or "npx", "playwright", "test"], ROOT / "apps/web", "browser.log")]
    for command, cwd, log_name in commands:
        print("Running", log_name, flush=True)
        with (output / log_name).open("wb") as log:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise SystemExit(result.returncode)
    shutil.copy2(ROOT / "var/browser-results.json", output / "browser.json")
    from packages.sync.verification import regression_evidence
    write(output / "regression-evidence.json", regression_evidence(ROOT))


if __name__ == "__main__":
    main()
