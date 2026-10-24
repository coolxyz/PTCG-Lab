"""Offline wheel acceptance in a clean interpreter, outside both checkouts.

Only explicit operator-owned bundle/cases/frozen baseline are read. This runner
never starts or stops any application service. Private records stay in output.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REQUIRED = {
    "prize",
    "face-up-prize",
    "replacement",
    "deck-search",
    "non-turn-actor",
    "hidden-selection",
    "terminal",
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "cases", "legacy-root", "wheels", "wheelhouse", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--pin", required=True)
    a = p.parse_args()
    for name in ("bundle", "cases", "legacy_root", "wheels", "wheelhouse", "output"):
        setattr(a, name, getattr(a, name).resolve())
    a.output.mkdir(parents=True, exist_ok=False)
    wheel_paths = [
        next(a.wheels.glob(n + "-*.whl"))
        for n in ("ptcg_battle_core", "ptcg_engine_provider", "ptcg_battle_protocol")
    ]
    with tempfile.TemporaryDirectory(prefix="ptcg-p2d-wheel-") as temp:
        root = Path(temp)
        env = root / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
        python = env / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(a.wheelhouse),
                *[str(w) for w in wheel_paths],
            ],
            check=True,
            cwd=root,
        )
        worker = root / "p2d_worker.py"
        shutil.copyfile(Path(__file__).with_name("p2d_worker.py"), worker)

        def run(mode, output, extra=()):
            if mode == "legacy":
                command = [
                    str(python),
                    "-c",
                    'import sys; from pathlib import Path; p=Path(sys.argv.pop(1)); exec(compile(p.read_text(),str(p),"exec"))',
                    str(worker),
                ]
                cwd = a.legacy_root
            else:
                command = [str(python), "-I", str(worker)]
                cwd = root
            command += [
                "--mode",
                mode,
                "--cases",
                str(a.cases),
                "--output",
                str(output),
                "--bundle",
                str(a.bundle),
                "--pin",
                a.pin,
                *extra,
            ]
            with output.with_suffix(".log").open("w") as log:
                subprocess.run(
                    command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, check=True
                )

        old = a.output / "legacy.json"
        new = a.output / "core.json"
        run("legacy", old)
        run("core", new, ["--expected", str(old)])
        core = json.loads(new.read_text())
        resumes = []
        # Choose real engine pause records, not synthetic injected board snapshots.
        for category in ("deck-search", "prize", "replacement", "non-turn-actor", "setup:active"):
            cp = next(c for c in core["checkpoints"] if category in c["tags"])
            source = a.output / ("checkpoint-" + str(len(resumes)) + ".json")
            source.write_text(json.dumps(cp))
            output = a.output / ("resume-" + str(len(resumes)) + ".json")
            run("resume", output, ["--expected", str(old), "--checkpoint", str(source)])
            resumes.append(json.loads(output.read_text()))
        installed = json.loads(
            subprocess.check_output(
                [str(python), "-m", "pip", "list", "--format=json"], text=True, cwd=root
            )
        )
        assert not {d["name"].lower() for d in installed} & {
            "fastapi",
            "torch",
            "numpy",
            "pyside6",
            "uvicorn",
        }
        assert len(core["natural"]) >= 4 and len(core["directed"]) >= 8
        assert REQUIRED <= set(core["coverage"])
        commands = sum(len(m["commands"]) for m in core["natural"])
        assert core["interleavedCommands"] == commands
        summary = dict(
            schema=1,
            status="passed",
            naturalMatches=len(core["natural"]),
            commands=commands,
            comparedFrames=sum(len(m["rows"]) for m in core["natural"]),
            directedScenes=len(core["directed"]),
            restoreCheckpoints=len(core["checkpoints"]),
            crossProcessResumes=resumes,
            interleavedCommands=commands,
            coverage=core["coverage"],
            ruleReleaseId=core["ruleReleaseId"],
            installed=installed,
            definitionsUnchanged=core["definitionsUnchanged"],
            globalRngUnchanged=core["globalRngUnchanged"],
            productionRoutingChanged=False,
            workerSha256=hashlib.sha256(worker.read_bytes()).hexdigest(),
            artifacts={
                w.name: hashlib.sha256(w.read_bytes()).hexdigest() for w in wheel_paths
            },
        )
        (a.output / "ACCEPTANCE.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary))


if __name__ == "__main__":
    main()
