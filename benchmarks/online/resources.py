"""Operate only a newly owned P7 cgroup; never change the Docker root or siblings."""

import argparse
import json
from pathlib import Path
import re
import time

NAME = re.compile(r"ptcg-p7-[a-f0-9]{12}\Z")


def group(root, name):
    if not NAME.fullmatch(name):
        raise ValueError("INVALID_P7_RESOURCE_GROUP")
    return Path(root) / name


def numbers(path):
    return {
        k: int(v) for k, v in (line.split() for line in path.read_text().splitlines())
    }


def sample(root, name):
    path = group(root, name)
    return {
        "time": time.time(),
        "cpu": numbers(path / "cpu.stat"),
        "memoryBytes": int((path / "memory.current").read_text()),
        "memoryPeakBytes": int((path / "memory.peak").read_text()),
        "memoryEvents": numbers(path / "memory.events"),
        "swapBytes": int((path / "memory.swap.current").read_text()),
        "cpuMax": (path / "cpu.max").read_text().strip(),
        "memoryMax": int((path / "memory.max").read_text()),
        "cpus": (path / "cpuset.cpus.effective").read_text().strip(),
    }


def create(root, name):
    path = group(root, name)
    path.mkdir()  # Refuse existing groups, including any prior run.
    (path / "cpu.max").write_text("200000 100000")
    (path / "memory.max").write_text(str(2 * 1024**3))
    (path / "memory.swap.max").write_text("0")
    (path / "pids.max").write_text("1024")
    (path / "cgroup.subtree_control").write_text("+cpu +memory +pids +cpuset")
    return {"group": name, "vcpus": 2, "memoryBytes": 2 * 1024**3, "swapBytes": 0}


def remove(root, name):
    path = group(root, name)
    if numbers(path / "cgroup.events")["populated"]:
        raise ValueError("P7_RESOURCE_GROUP_STILL_POPULATED")
    path.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "sample", "remove", "watch"])
    parser.add_argument("name")
    parser.add_argument("--root", default="/cgroup")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.action == "create":
        print(json.dumps(create(args.root, args.name)))
    elif args.action == "sample":
        print(json.dumps(sample(args.root, args.name)))
    elif args.action == "remove":
        remove(args.root, args.name)
    else:
        with Path(args.output).open("a", buffering=1) as out:
            while group(args.root, args.name).exists():
                out.write(json.dumps(sample(args.root, args.name)) + "\n")
                time.sleep(1)


if __name__ == "__main__":
    main()
