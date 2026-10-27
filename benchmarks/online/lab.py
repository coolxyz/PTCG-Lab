"""Explicit, disposable P7 deployment. Mutations are limited to its unique owner label."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from benchmarks.online.resources import NAME  # noqa: E402
from benchmarks.online.guard import Guard, running  # noqa: E402
from benchmarks.online.identity import runtime_digest  # noqa: E402

BASE_IMAGE = "sha256:29ad6123c04a878e0d60c000436187adc38fcabadaa9f99ae6916e9eac462d43"
LABEL = "io.ptcg.p7.owner"


def docker(args, *, capture=False, **kwargs):
    result = subprocess.run(
        ["docker", *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs,
    )
    if result.returncode:
        path = ROOT / "var/p7/docker-errors.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as output:
            path.chmod(0o600)
            output.write(result.stderr + result.stdout)
        raise subprocess.CalledProcessError(result.returncode, ["docker", args[0]])

    return result.stdout.decode().strip() if capture else None


def metadata(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / "var/p7") or path.is_symlink():
        raise ValueError("DEDICATED_P7_DIRECTORY_REQUIRED")
    value = json.loads((path / "lab.json").read_text())
    if value["schema"] != 1 or not NAME.fullmatch(value["project"]):
        raise ValueError("INVALID_P7_OWNER")
    return path, value


def inspect_owned(value, service):
    name = value["project"] + "-" + service + "-1"
    obj = json.loads(docker(["inspect", name], capture=True))[0]
    labels = obj["Config"]["Labels"]
    assert (
        labels.get(LABEL) == value["project"]
        and labels.get("com.docker.compose.service") == service
    )
    return name, obj


def compose(path, value, *args):
    return docker(
        ["compose", "-p", value["project"], "-f", str(path / "compose.json"), *args]
    )


def cgroup(path, value, action):
    return json.loads(
        docker(
            [
                "run",
                "--rm",
                "--network",
                "none",
                "--cgroupns",
                "host",
                *(["--privileged"] if action in ("create", "remove") else []),
                "--user",
                "0",
                "--cpus",
                "0.1",
                "--memory",
                "64m",
                "--mount",
                "type=bind,source=/sys/fs/cgroup,target=/cgroup"
                + (",readonly" if action == "sample" else ""),
                "--mount",
                f"type=bind,source={ROOT / 'benchmarks/online'},target=/bench,readonly",
                "--entrypoint",
                "python",
                BASE_IMAGE,
                "/bench/resources.py",
                action,
                value["project"],
            ],
            capture=True,
        )
        or "{}"
    )


def prepare(directory, inputs_path, workers):
    path = Path(directory).resolve()
    if not path.is_relative_to(ROOT / "var/p7") or workers not in (1, 2):
        raise ValueError("DEDICATED_P7_DIRECTORY_REQUIRED")
    path.mkdir(parents=True, mode=0o700, exist_ok=False)
    inputs = json.loads(Path(inputs_path).read_text())
    project = "ptcg-p7-" + uuid.uuid4().hex[:12]
    for name in ("inputs", "tls", "measure", "results", "state"):
        (path / name).mkdir(
            mode=0o777 if name in ("measure", "results", "state") else 0o755
        )
        if name in ("measure", "results", "state"):
            (path / name).chmod(0o777)
    (path / "inputs/decks.json").write_text(json.dumps(inputs))
    (path / "inputs/decks.json").chmod(0o444)
    config = path / "tls/openssl.cnf"
    config.write_text(
        "[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=p7-api\n[ext]\nsubjectAltName=DNS:p7-api,IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\nextendedKeyUsage=serverAuth\n"
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "7",
            "-keyout",
            str(path / "tls/server-key.pem"),
            "-out",
            str(path / "tls/server.pem"),
            "-config",
            str(config),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for p in (path / "tls").iterdir():
        p.chmod(0o444)
    password = secrets.token_urlsafe(32)
    env = path / "database.env"
    env.write_text(
        "POSTGRES_USER=ptcg_p7\nPOSTGRES_DB=ptcg_p7\nPOSTGRES_PASSWORD="
        + password
        + "\n"
    )
    env.chmod(0o600)
    env = path / "api.env"
    env.write_text(
        "PTCG_ONLINE_V2_DATABASE_URL=postgresql://ptcg_p7:"
        + password
        + "@database:5432/ptcg_p7\n"
    )
    env.chmod(0o600)
    image = "ptcg-lab-p7-bench:" + project[-12:]
    settings = dict(
        SERVER_ID=project,
        PUBLIC_ORIGIN="https://p7-api:8784",
        RULE_DIRECTORY="/rules",
        RULE_MANIFEST_SHA256=inputs["pin"],
        APPROVED_RULE_RELEASE_ID=inputs["release"],
        MAX_INFLIGHT=32,
        MAX_STREAMS=256,
        CONNECTION_RESERVE=8,
        WORKER_COUNT=workers,
        WORKER_COLD_LIMIT=1,
        WORKER_CACHE_ENTRIES=8,
        WORKER_RSS_MB=256,
        CREATE_PER_MINUTE=1000,
        JOIN_PER_MINUTE=1000,
        REQUESTS_PER_MINUTE=20000,
        GLOBAL_PER_MINUTE=100000,
        REPLAY_PER_MINUTE=10000,
        REPLAY_GLOBAL_PER_MINUTE=10000,
        MAX_ROOMS=256,
        MAX_SESSIONS=1024,
        MAX_LOBBY_RECEIPTS=20000,
    )
    envs = {"PTCG_ONLINE_V2_" + k: str(v) for k, v in settings.items()}
    common = dict(
        labels={LABEL: project},
        cgroup_parent="/" + project,
        cpuset="6-7",
        restart="no",
        networks=["test"],
    )
    database = {
        **common,
        "image": "postgres:17",
        "env_file": [str(path / "database.env")],
        "volumes": ["database:/var/lib/postgresql/data"],
        "mem_limit": "512m",
        "cpus": 0.5,
        "healthcheck": {
            "test": ["CMD-SHELL", "pg_isready -U ptcg_p7 -d ptcg_p7"],
            "interval": "2s",
            "timeout": "2s",
            "retries": 30,
        },
    }
    api = {
        **common,
        "image": image,
        "command": ["/bench/observe.py"],
        "environment": envs,
        "env_file": [str(path / "api.env")],
        "volumes": [
            f"{Path(inputs['directory']).resolve()}:/rules:ro",
            f"{path / 'tls'}:/tls:ro",
            f"{path / 'measure'}:/measure",
        ],
        "networks": {"test": {"aliases": ["p7-api"]}},
        "mem_limit": "1g",
        "cpus": 1.25,
        "read_only": True,
        "tmpfs": ["/tmp"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "stop_grace_period": "45s",
        "depends_on": {"database": {"condition": "service_healthy"}},
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                'import ssl,urllib.request; r=urllib.request.Request("https://127.0.0.1:8784/readyz",headers={"Host":"p7-api:8784"}); urllib.request.urlopen(r,context=ssl.create_default_context(cafile="/tls/server.pem"),timeout=4)',
            ],
            "interval": "10s",
            "timeout": "5s",
            "retries": 4,
            "start_period": "20s",
        },
    }
    driver = {
        "image": image,
        "labels": {LABEL: project},
        "networks": ["test"],
        "mem_limit": "512m",
        "cpus": 1.0,
        "cpuset": "8",
        "read_only": True,
        "tmpfs": ["/tmp"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "volumes": [
            f"{path / 'inputs'}:/inputs:ro",
            f"{path / 'tls'}:/tls:ro",
            f"{path / 'measure'}:/measure",
            f"{path / 'state'}:/state",
            f"{path / 'results'}:/results",
        ],
    }
    manifest = dict(
        schema=1,
        protected=running(),
        project=project,
        image=image,
        baseImage=BASE_IMAGE,
        workers=workers,
        created=datetime.now(timezone.utc).isoformat(),
        settings=settings,
        resourceBudget=dict(
            vcpus=2,
            memoryBytes=2 * 1024**3,
            swap=0,
            apiVcpus=1.25,
            apiMemoryBytes=1024**3,
            databaseVcpus=0.5,
            databaseMemoryBytes=512 * 1024**2,
            remainingVcpus=0.25,
            remainingMemoryBytes=512 * 1024**2,
        ),
        tlsInApi=True,
        driverBudget=dict(vcpus=1, memoryBytes=512 * 1024**2, separateFromTarget=True),
    )
    (path / "compose.json").write_text(
        json.dumps(
            dict(
                name=project,
                services=dict(database=database, api=api, driver=driver),
                networks=dict(test={"internal": True}),
                volumes=dict(database={"labels": {LABEL: project}}),
            ),
            indent=2,
        )
        + "\n"
    )
    (path / "lab.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (path / "measure/phase").write_text("setup")
    (path / "measure/phase").chmod(0o666)
    print(
        json.dumps(
            dict(status="prepared", project=project, workers=workers, internalOnly=True)
        )
    )


def ready(path, value):
    name, _ = inspect_owned(value, "api")
    code = """import ssl,json,urllib.request
from urllib.error import HTTPError
r=urllib.request.Request('https://127.0.0.1:8784/readyz',headers={'Host':'p7-api:8784'})
try:
 with urllib.request.urlopen(r,context=ssl.create_default_context(cafile='/tls/server.pem'),timeout=3) as response: print(response.read().decode())
except HTTPError as error: print(error.read().decode())
"""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            result = json.loads(
                docker(["exec", name, "python", "-c", code], capture=True)
            )
            if result == dict(ready=True, database=True, rules=True):
                return
        except (subprocess.CalledProcessError, ValueError):
            pass
        time.sleep(1)
    raise ValueError("P7_API_NOT_READY")


def start(path, value):
    record = path / "resource-budget.json"
    if record.exists():
        budget = json.loads(record.read_text())
        assert budget["group"] == value["project"]
        actual = cgroup(path, value, "sample")
        assert (
            actual["cpuMax"] == "200000 100000" and actual["memoryMax"] == 2 * 1024**3
        )
    else:
        budget = cgroup(path, value, "create")
        record.write_text(json.dumps(budget, indent=2) + "\n")
    assert (
        docker(
            ["image", "inspect", "ptcg-lab-online-api:p6e", "--format", "{{.Id}}"],
            capture=True,
        )
        == BASE_IMAGE
    )
    docker(
        [
            "build",
            "-f",
            str(ROOT / "benchmarks/online/Dockerfile"),
            "--build-arg",
            "API_IMAGE=ptcg-lab-online-api:p6e",
            "-t",
            value["image"],
            str(ROOT),
        ]
    )
    compose(path, value, "up", "-d", "database")
    for attempt in range(3):
        try:
            compose(
                path,
                value,
                "run",
                "--rm",
                "--no-deps",
                "api",
                "-m",
                "ptcg_online_server.migrate",
            )
            break
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            time.sleep(2)
    compose(path, value, "up", "-d", "--no-deps", "api")
    ready(path, value)
    for service in ("api", "database"):
        _, item = inspect_owned(value, service)
        assert item["HostConfig"]["CgroupParent"] == "/" + value["project"]
    image_id = docker(
        ["image", "inspect", value["image"], "--format", "{{.Id}}"], capture=True
    )
    runtime = docker(
        [
            "run",
            "--rm",
            "--network",
            "none",
            "--cpus",
            "0.1",
            "--memory",
            "64m",
            value["image"],
            "-c",
            "from identity import runtime_digest; print(runtime_digest('/bench'))",
        ],
        capture=True,
    )
    assert runtime == runtime_digest(ROOT)
    value.update(imageId=image_id, runtimeSourceSha256=runtime)
    (path / "lab.json").write_text(json.dumps(value, indent=2) + "\n")
    stats = cgroup(path, value, "sample")
    (path / "resource-start.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(dict(status="ready", workers=value["workers"], limits=budget)))


def run_driver(path, value, mode, extra):
    inspect_owned(value, "api")
    inspect_owned(value, "database")
    filename = mode + "-" + uuid.uuid4().hex[:8] + ".json"
    protected = value.get("protected", [])
    if "protected" not in value:
        raise ValueError("PROTECTED_SERVICE_BASELINE_REQUIRED")

    def stop_driver():
        ids = docker(
            [
                "ps",
                "-q",
                "--filter",
                "label=" + LABEL + "=" + value["project"],
                "--filter",
                "label=com.docker.compose.service=driver",
            ],
            capture=True,
        ).split()
        for identity in ids:
            docker(["stop", "--time", "3", identity])

    with Guard(path, protected, lambda: cgroup(path, value, "sample"), stop_driver):
        compose(
            path,
            value,
            "run",
            "--rm",
            "--no-deps",
            "driver",
            "/bench/driver.py",
            mode,
            "--output",
            "/results/" + filename,
            *extra,
        )
    result = json.loads((path / "results" / filename).read_text())
    print(json.dumps(dict(status=result["status"], mode=mode, result=filename)))
    return result


def fault(path, value):
    result = []
    result.append(run_driver(path, value, "checkpoint", []))
    name, _ = inspect_owned(value, "api")
    kill = """import os,signal; from pathlib import Path
count=0
for p in Path('/proc').iterdir():
 if not p.name.isdigit() or int(p.name)==os.getpid(): continue
 try: cmd=(p/'cmdline').read_bytes().split(b'\\0')
 except OSError: continue
 if b'ptcg_online_server.rule_worker' in cmd: os.kill(int(p.name),signal.SIGKILL); count+=1
assert count>0
"""
    docker(["exec", name, "python", "-c", kill])
    result.append(run_driver(path, value, "recover", []))
    docker(["restart", "--time", "30", name])
    ready(path, value)
    result.append(run_driver(path, value, "recover", []))
    database, _ = inspect_owned(value, "database")
    docker(["pause", database])
    try:
        result.append(run_driver(path, value, "unavailable", []))
    finally:
        docker(["unpause", database])
    ready(path, value)
    result.append(run_driver(path, value, "recover", []))
    proof = json.loads(
        docker(
            [
                "exec",
                database,
                "psql",
                "-U",
                "ptcg_p7",
                "-d",
                "ptcg_p7",
                "-Atc",
                "SELECT json_build_object('matches',count(*),'seq',max(match_seq),'receipts',(SELECT count(*) FROM ptcg_v2.match_receipts),'frames',(SELECT count(*) FROM ptcg_v2.match_frames)) FROM ptcg_v2.matches",
            ],
            capture=True,
        )
    )
    # Run faults only against a fresh lab so the SQL proof is unambiguous.
    assert (
        proof["matches"] == 1
        and proof["receipts"] == proof["seq"]
        and proof["frames"] == 2 * (proof["seq"] + 1)
    )
    report = dict(
        schema=1,
        status="passed",
        scope="idle worker death, API restart, paused dedicated DB and exact-request recovery",
        preciseInflightCommitBoundary=False,
        steps=result,
        sql=proof,
        capacityValidated=False,
    )
    (path / "fault-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(dict(status="passed", faultChecks=3, sql=proof)))


def stop(path, value):
    # Refuse teardown if a same-named container does not have our ownership label.
    for service in ("api", "database"):
        inspect_owned(value, service)
    database, _ = inspect_owned(value, "database")
    backup = path / "database.dump"
    with backup.open("wb") as output:
        backup.chmod(0o600)
        subprocess.run(
            [
                "docker",
                "exec",
                database,
                "pg_dump",
                "-U",
                "ptcg_p7",
                "-d",
                "ptcg_p7",
                "-Fc",
            ],
            check=True,
            stdout=output,
            stderr=subprocess.DEVNULL,
        )
    (path / "resource-final.json").write_text(
        json.dumps(cgroup(path, value, "sample"), indent=2) + "\n"
    )
    compose(path, value, "down", "--volumes", "--timeout", "45")
    cgroup(path, value, "remove")
    print(json.dumps(dict(status="removed", project=value["project"])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["prepare", "start", "sample", "load", "fault", "stop"]
    )
    parser.add_argument("--directory", required=True)
    parser.add_argument("--inputs")
    parser.add_argument("--workers", type=int, default=1)
    args, extra = parser.parse_known_args()
    try:
        if args.action == "prepare":
            return prepare(args.directory, args.inputs, args.workers)
        path, value = metadata(args.directory)
        if args.action == "start":
            start(path, value)
        elif args.action == "sample":
            print(json.dumps(cgroup(path, value, "sample")))
        elif args.action == "load":
            run_driver(path, value, "load", extra)
        elif args.action == "fault":
            fault(path, value)
        else:
            stop(path, value)
    except Exception as error:
        print(
            json.dumps(dict(status="failed", error=type(error).__name__)),
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
