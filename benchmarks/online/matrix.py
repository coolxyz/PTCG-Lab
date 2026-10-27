"""Serial P7-B matrix. Fresh owned database per sample, with guarded LAN protection.

Never ascend beyond a failed level. Never silently retry or relabel a failed run.
Progress stays private; publication is an explicit aggregate-report operation.
"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from benchmarks.online import lab  # noqa: E402
from benchmarks.online.acceptance import summarize  # noqa: E402
from benchmarks.online.identity import runtime_digest, tools_digest  # noqa: E402


def plan(levels, workers, repeats):
    if levels not in ([10], [10, 25], [10, 25, 50], [10, 25, 50, 100]):
        raise ValueError("ASCENDING_MATRIX_FROM_TEN_REQUIRED")
    if workers not in ([1], [2], [1, 2]) or repeats < 3:
        raise ValueError("ORDERED_WORKERS_AND_THREE_REPEATS_REQUIRED")
    return [(w, n, r) for w in workers for n in levels for r in range(1, repeats + 1)]


def run(directory, inputs, levels, workers, repeats, publish=False, diagnostics=()):
    jobs = plan(levels, workers, repeats)
    path = Path(directory).resolve()
    if not path.is_relative_to(ROOT / "var/p7"):
        raise ValueError("PRIVATE_P7_DIRECTORY_REQUIRED")
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    identity = runtime_digest(ROOT), tools_digest(ROOT)
    progress = dict(
        schema=1,
        stage="P7-B",
        status="running",
        capacityValidated=False,
        levels=levels,
        workers=workers,
        repeats=repeats,
        samples=[],
        stoppedWorkers=[],
    )

    def save():
        temp = path / "matrix.tmp"
        temp.write_text(json.dumps(progress, indent=2) + "\n")
        temp.replace(path / "matrix.json")

    save()
    failed_levels = {}
    complete_paths = []
    try:
        for worker, matches, repeat in jobs:
            if worker in failed_levels and matches > failed_levels[worker]:
                continue
            if identity != (runtime_digest(ROOT), tools_digest(ROOT)):
                raise ValueError("BENCHMARK_SOURCE_CHANGED_DURING_MATRIX")
            name = f"m{matches}-w{worker}-r{repeat}"
            sample = path / name
            progress["active"] = name
            save()
            # Same initial rule seeds, public match identities and driver jitter
            # for the corresponding worker comparison. No production entropy changes.
            seed = 710000 + matches * 10 + repeat
            lab.prepare(sample, inputs, worker, seed)
            owned, value = lab.metadata(sample)
            started = False
            try:
                lab.start(owned, value)
                started = True
                lab.run_driver(
                    owned,
                    value,
                    "load",
                    [
                        "--label",
                        "matrix",
                        "--matches",
                        str(matches),
                        "--warmup",
                        "300",
                        "--steady",
                        "1200",
                        "--seed",
                        str(seed),
                    ],
                )
            finally:
                if started:
                    lab.stop(owned, value)
                else:
                    lab.cleanup_failed_start(owned, value)
            result = summarize(sample, ROOT)
            result["sample"] = name
            progress["samples"].append(result)
            complete_paths.append(sample)
            progress.pop("active", None)
            if result["status"] != "passed":
                failed_levels[worker] = matches
                if worker not in progress["stoppedWorkers"]:
                    progress["stoppedWorkers"].append(worker)
            save()
            if publish:
                from scripts.migration.p7b_report import generate

                generate(complete_paths, diagnostics)
            print(
                json.dumps(
                    dict(
                        sample=name,
                        status=result["status"],
                        p95=result["asgiP95UpperSeconds"],
                    )
                ),
                flush=True,
            )
        progress["status"] = (
            "threshold_stopped" if failed_levels else "planned_samples_finished"
        )
    except BaseException as error:
        progress.update(status="aborted", error=type(error).__name__)
        raise
    finally:
        save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--levels", nargs="+", type=int, default=[10, 25, 50, 100])
    parser.add_argument("--workers", nargs="+", type=int, default=[1, 2])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Update only the allowlisted aggregate release report after each complete sample",
    )
    parser.add_argument("--diagnostics", nargs="*", default=[])
    args = parser.parse_args()
    try:
        run(
            args.directory,
            args.inputs,
            args.levels,
            args.workers,
            args.repeats,
            args.publish,
            args.diagnostics,
        )
    except Exception as error:
        print(
            json.dumps(dict(status="aborted", error=type(error).__name__)),
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
