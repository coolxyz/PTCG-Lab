"""Run P4-A acceptance in an ephemeral, loopback-only PostgreSQL container.

Only the exact container created here is removed. Existing containers/volumes and
all product services are outside this script's lifecycle.
"""

import argparse
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def run(output, python, image=None):
    output = Path(output).resolve()
    if not any(
        output.is_relative_to(ROOT / name) for name in ("var/p4a", "var/p4b", "var/p4c")
    ):
        raise ValueError("Acceptance output must be below var/p4a, var/p4b or var/p4c")
    output.mkdir(parents=True, exist_ok=False)
    password = secrets.token_urlsafe(32)
    envfile = output / "postgres.env"
    envfile.write_text(
        f"POSTGRES_USER=ptcg_p4a\nPOSTGRES_DB=ptcg_p4a\nPOSTGRES_PASSWORD={password}\n"
    )
    envfile.chmod(0o600)
    dsn = output / "database-url"
    # Port 55484 belongs only to this opt-in acceptance environment.
    dsn.write_text(f"postgresql://ptcg_p4a:{password}@127.0.0.1:55484/ptcg_p4a")
    dsn.chmod(0o600)
    container = subprocess.check_output(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--pull",
            "never",
            "--name",
            "ptcg-lab-p4a-" + uuid.uuid4().hex[:12],
            "--label",
            "ptcg.acceptance=p4a",
            "--memory",
            "256m",
            "--cpus",
            "0.5",
            "--publish",
            "127.0.0.1:55484:5432",
            *(["--publish", "127.0.0.1:8784:8784"] if image else []),
            "--tmpfs",
            "/var/lib/postgresql/data",
            "--env-file",
            str(envfile),
            "postgres:17",
        ],
        text=True,
    ).strip()
    try:
        for _ in range(30):
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_isready",
                    "-U",
                    "ptcg_p4a",
                    "-d",
                    "ptcg_p4a",
                ],
                capture_output=True,
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Acceptance PostgreSQL did not become ready")
        return subprocess.call(
            [
                str(python),
                "-m",
                "pytest",
                "tests/online_v2",
                "-q",
                "--junitxml",
                str(output / "results.xml"),
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "PTCG_V2_TEST_DSN_FILE": str(dsn),
                **(
                    {
                        "PTCG_V2_TEST_IMAGE": image,
                        "PTCG_V2_TEST_DB_CONTAINER": container,
                    }
                    if image
                    else {}
                ),
                "PYTHONPATH": str(ROOT / "apps/online-server/src"),
            },
        )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        envfile.unlink(missing_ok=True)
        dsn.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument(
        "--image", help="Optional freshly built API image for container acceptance"
    )
    args = parser.parse_args()
    # resolve() preserves the venv path; don't resolve the interpreter symlink.
    raise SystemExit(run(args.output, args.python.absolute(), args.image))
