"""Explicit deterministic lobby entropy for private, disposable benchmark images only.

Authentication tokens, invite codes and production files retain their original entropy.
The wire driver never reads a rule seed or hidden game state to choose commands.
"""

from hashlib import sha256
from threading import Lock


def install(module, seed):
    original = module.secrets
    lock = Lock()
    counters = dict(identity=0, rule=0)

    def value(kind):
        with lock:
            index = counters[kind]
            counters[kind] += 1
        return sha256(f"p7-fixture-v1:{seed}:{kind}:{index}".encode()).hexdigest()

    class Entropy:
        def __getattr__(self, name):
            return getattr(original, name)

        def randbelow(self, maximum):
            if maximum != 9007199254740992:
                raise ValueError("UNEXPECTED_BENCHMARK_SEED_BOUND")
            return int(value("rule"), 16) % maximum

    module.uid = lambda: value("identity")[:32]
    module.secrets = Entropy()
