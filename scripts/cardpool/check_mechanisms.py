"""Plan/run tiered checks. Incremental evidence never overwrites release evidence."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def versions():
    from packages.battle.runtime import ENGINE_VERSION
    from packages.simulation.registry import VERSION as EFFECT_VERSION
    from packages.cardpool.scope import VERSION as SCOPE_VERSION

    return {
        "engine": ENGINE_VERSION,
        "effects": EFFECT_VERSION,
        "scope": SCOPE_VERSION,
        "catalog": json.loads(
            (ROOT / "data/catalog/catalog.json").read_text(encoding="utf8")
        )["version"],
    }


def plan(tier, ui=False, extra_tests=()):
    config = json.loads(
        (ROOT / "data/cardpool/mechanism-workflow.json").read_text(encoding="utf8")
    )
    if tier not in ("targeted", "integration", "release"):
        raise ValueError("UNKNOWN_CHECK_TIER")
    for test in extra_tests:
        path = (ROOT / test.split("::", 1)[0]).resolve()
        if (
            not path.is_relative_to(ROOT / "tests")
            or not path.is_file()
            or path.suffix != ".py"
        ):
            raise ValueError("TEST_PATH_MUST_BE_AN_EXISTING_REPOSITORY_TEST")
    selected_tests = list(dict.fromkeys([*config[tier + "Tests"], *extra_tests]))
    # Each incremental run gets its own directory; successful release evidence
    # remains bound to its original engine/catalog/effect versions.
    directory = f"artifacts/cardpool/checks/{tier}-{time.time_ns()}"
    commands = [
        {
            "cwd": ".",
            "args": [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "pytest",
                *selected_tests,
                "--import-mode=importlib",
                "-q",
                "--basetemp=" + directory + "/pytest-temp",
                "-o",
                "cache_dir=" + directory + "/pytest-cache",
                "--junitxml=" + directory + "/backend.xml",
            ],
        }
    ]
    if tier != "targeted":
        commands.append(
            {
                "cwd": ".",
                "args": [
                    sys.executable,
                    "-X",
                    "utf8",
                    "scripts/cardpool/combinations.py",
                    "--games",
                    str(config[tier + "Games"]),
                    "--workers",
                    "4",
                    "--output",
                    directory + "/combinations.json",
                ],
            }
        )
    if ui or tier == "release":
        prefix = ["cmd", "/d", "/c", "npm"] if os.name == "nt" else ["npm"]
        commands += [
            {"cwd": "apps/web", "args": prefix + ["run", "build"]},
            {"cwd": "apps/web", "args": prefix + ["run", "test:e2e"]},
        ]
    return {
        "tier": tier,
        "directory": directory,
        "commands": commands,
        "updatesReleaseAcceptance": False,
        "note": "A passed check run is evidence only. Publication and final P4 acceptance remain separate.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tier", choices=["targeted", "integration", "release"], default="targeted"
    )
    parser.add_argument("--ui", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--test",
        action="append",
        default=[],
        help="Include a new family test file or pytest node; repeatable",
    )
    args = parser.parse_args()
    result = plan(args.tier, args.ui, args.test)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if not args.execute:
        return 0
    directory = ROOT / result["directory"]
    directory.mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "runtime/engine"), str(ROOT), str(ROOT / "tests/rules")]
    )
    result["results"] = []
    result["versions"] = versions()
    for i, command in enumerate(result["commands"]):
        with (directory / f"{i}.log").open("w", encoding="utf8") as out:
            try:
                code = subprocess.run(
                    command["args"],
                    cwd=ROOT / command["cwd"],
                    env=env,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                ).returncode
            except OSError as exc:
                out.write(str(exc))
                code = 127
        result["results"].append({"step": i, "exitCode": code})
        if (
            command["args"][-1] == "test:e2e"
            and (ROOT / "var/browser-results.json").exists()
        ):
            (directory / "browser.json").write_bytes(
                (ROOT / "var/browser-results.json").read_bytes()
            )
        if code:
            break
    result["status"] = (
        "PASS"
        if len(result["results"]) == len(result["commands"])
        and all(r["exitCode"] == 0 for r in result["results"])
        else "FAIL"
    )
    (directory / "run.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )
    return int(result["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
