"""Hash the measured observer/driver/client closure independently of checkout paths."""

from hashlib import sha256
from pathlib import Path


def digest_files(items):
    value = sha256()
    for name, path in sorted(items):
        value.update(name.encode() + b"\0" + path.read_bytes())
    return value.hexdigest()


def runtime_digest(root):
    root = Path(root)
    if (root / "driver.py").exists():
        base = root
        client = root / "ptcg_online_client"
    else:
        base = root / "benchmarks/online"
        client = root / "packages/online-client/src/ptcg_online_client"
    items = [
        (name, base / name)
        for name in ("driver.py", "observe.py", "measure.py", "fixture.py")
    ]
    items += [
        ("ptcg_online_client/" + p.relative_to(client).as_posix(), p)
        for p in client.rglob("*")
        if p.is_file() and p.suffix in (".py", ".json")
    ]
    return digest_files(items)


def tools_digest(root):
    base = Path(root) / "benchmarks/online"
    return digest_files(
        [
            (p.name, p)
            for p in base.iterdir()
            if p.is_file()
            and (
                p.suffix == ".py" or p.name in ("Dockerfile", "Dockerfile.dockerignore")
            )
        ]
    )
