"""Bounded POSIX pipe exchange; a timeout kills and reaps only this generation."""

import json
import os
import selectors
import subprocess
import time
import uuid
from threading import Lock

from ptcg_engine_provider.values import canonical


class WorkerSlot:
    def __init__(self, args, options):
        self.args, self.options = args, options
        self.lock = Lock()
        self.process = None
        self.generation = None
        self.sequence = 0

    def close(self):
        child, self.process = self.process, None
        self.generation = None
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
            child.stdin.close()
            child.stdout.close()

    def exchange(self, request, timeout):
        if self.process is None or self.process.poll() is not None:
            self.close()
            self.process = subprocess.Popen(
                self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                **self.options,
            )
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
            self.generation, self.sequence = uuid.uuid4().hex, 0
        self.sequence += 1
        generation, sequence = self.generation, self.sequence
        raw = (
            canonical({**request, "generation": generation, "requestId": sequence})
            + b"\n"
        )
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("WORKER_INPUT_LIMIT")
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            output = self.process.stdin.fileno()
            selector.register(output, selectors.EVENT_WRITE)
            offset = 0
            while offset < len(raw):
                if not selector.select(max(0, deadline - time.monotonic())):
                    raise TimeoutError("WORKER_TIMEOUT")
                try:
                    offset += os.write(output, raw[offset : offset + 65536])
                except BlockingIOError:
                    continue
            selector.unregister(output)
            source = self.process.stdout.fileno()
            selector.register(source, selectors.EVENT_READ)
            data = bytearray()
            while True:
                if not selector.select(max(0, deadline - time.monotonic())):
                    raise TimeoutError("WORKER_TIMEOUT")
                try:
                    chunk = os.read(source, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise ValueError("WORKER_EOF")
                data.extend(chunk)
                if len(data) > 8 * 1024 * 1024 + 1:
                    raise ValueError("WORKER_OUTPUT_LIMIT")
                if b"\n" in chunk:
                    if not data.endswith(b"\n") or data.count(b"\n") != 1:
                        raise ValueError("WORKER_FRAME_INVALID")
                    response = json.loads(data)
                    if (
                        not isinstance(response, dict)
                        or response.get("generation") != generation
                        or response.get("requestId") != sequence
                        or not isinstance(response.get("result"), dict)
                    ):
                        raise ValueError("STALE_WORKER_RESULT")
                    return response["result"]
