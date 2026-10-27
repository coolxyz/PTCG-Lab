"""Protect pre-existing services and sample only the owned P7 resource group."""

import json
from pathlib import Path
import ssl
import subprocess
from threading import Event, Thread
import time
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener


def running():
    ids = subprocess.check_output(["docker", "ps", "-q"], text=True).split()
    if not ids:
        return []
    objects = json.loads(subprocess.check_output(["docker", "inspect", *ids]))
    result = []
    for item in objects:
        if item["Config"]["Labels"].get("io.ptcg.p7.owner"):
            continue
        value = dict(
            id=item["Id"],
            name=item["Name"],
            started=item["State"]["StartedAt"],
            image=item["Image"],
        )
        env = dict(v.split("=", 1) for v in item["Config"]["Env"] if "=" in v)
        ca = next(
            (m["Source"] for m in item["Mounts"] if m["Destination"] == "/tls/ca.pem"),
            None,
        )
        origin = env.get("PTCG_ONLINE_V2_PUBLIC_ORIGIN", "")
        if ca and origin.startswith("https://"):
            value.update(origin=origin, ca=ca)
        result.append(value)
    return result


def health(protected):
    latencies = []
    for item in protected:
        now = json.loads(subprocess.check_output(["docker", "inspect", item["id"]]))[0]
        if (
            now["Id"] != item["id"]
            or now["State"]["StartedAt"] != item["started"]
            or not now["State"]["Running"]
        ):
            raise ValueError("PROTECTED_SERVICE_CHANGED")
        if now["State"].get("Health", {}).get("Status", "healthy") != "healthy":
            raise ValueError("PROTECTED_SERVICE_UNHEALTHY")
        if "origin" in item:
            context = ssl.create_default_context(cafile=item["ca"])
            opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
            started = time.perf_counter()
            with opener.open(
                Request(item["origin"] + "/readyz"), timeout=3
            ) as response:
                assert json.load(response)["ready"] is True
            latencies.append(time.perf_counter() - started)
    return latencies


class Guard:
    def __init__(self, path, protected, sampler, stopper):
        self.path, self.protected, self.sampler, self.stopper = (
            Path(path),
            protected,
            sampler,
            stopper,
        )
        self.stop = Event()
        self.error = None
        self.samples = []
        self.rtts = []
        self.baseline = health(protected)
        self.thread = Thread(target=self.loop, daemon=True)

    def loop(self):
        slow = 0
        try:
            while not self.stop.is_set():
                values = health(self.protected)
                self.rtts.extend(values)
                slow = (
                    slow + 1
                    if any(
                        v > max(2, 10 * max(self.baseline, default=0.1)) for v in values
                    )
                    else 0
                )
                if slow >= 3:
                    raise ValueError("PROTECTED_SERVICE_LATENCY")
                sample = self.sampler()
                assert (
                    sample["cpuMax"] == "200000 100000"
                    and sample["memoryMax"] == 2 * 1024**3
                )
                assert (
                    not sample["swapBytes"] and not sample["memoryEvents"]["oom_kill"]
                )
                try:
                    sample["phase"] = (self.path / "measure/phase").read_text().strip()
                except OSError:
                    sample["phase"] = "setup"
                self.samples.append(sample)
                with (self.path / "resource-samples.jsonl").open("a") as out:
                    out.write(json.dumps(sample) + "\n")
                if self.stop.wait(5):
                    break
        except Exception as error:
            self.error = type(error).__name__
            self.stopper()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=15)
        final = health(self.protected)
        self.rtts.extend(final)
        result = dict(
            status="passed" if not self.error else "failed",
            protectedContainers=len(self.protected),
            protectedServicesUnchanged=True,
            protectedHealthChecks=len(self.rtts),
            protectedReadyMaximumSeconds=max(self.rtts, default=None),
            resourceSamples=len(self.samples),
            error=self.error,
        )
        (self.path / "guard-report.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        if self.error:
            raise ValueError("P7_GUARD_STOPPED_WORKLOAD")
