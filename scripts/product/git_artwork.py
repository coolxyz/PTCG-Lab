"""Maintainer-only pinned Git objects and exact artwork matching; no ZIP importer."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha1, sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

sys.dont_write_bytecode = True

DEFAULT_REPOSITORY = "https://github.com/duanxr/PTCG-CHS-Datasets"
MAX_IMAGE = 8 * 1024 * 1024


def encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    Path(path).write_bytes(encoded(value) + b"\n")


def compact(value):
    return re.sub(r"\s+", "", str(value or "")).casefold()


def number(value):
    value = compact(value).split("/")[0]
    return str(int(value)) if value.isdecimal() else value


def regular_path(path):
    value = PurePosixPath(path)
    if (
        not path
        or value.is_absolute()
        or value.as_posix() != path
        or any(p in {".", ".."} for p in value.parts)
        or any(c in path for c in ("\\", ":", "\0", "\n", "\r"))
    ):
        raise ValueError("Invalid upstream image path")
    return path


def app_root(path):
    path = Path(path).expanduser().resolve(strict=True)
    candidates = [path / "Contents/Resources/backend/app", path / "backend/app"]
    for candidate in candidates:
        if (candidate / "data/catalog/catalog.json").is_file():
            return candidate
    raise ValueError("--app must select a macOS .app or an unpacked Windows bundle")


class GitCache:
    def __init__(self, path, repository=DEFAULT_REPOSITORY, *, allow_local=False):
        if not (allow_local and Path(repository).is_dir()):
            if not re.fullmatch(
                r"https://github\.com/[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*",
                repository,
            ):
                raise ValueError(
                    "Use a public GitHub HTTPS repository without credentials"
                )
        self.path = Path(path).absolute()
        self.repository = repository
        self.local = allow_local
        self.repo = self.path / "repository"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(
            GIT_TERMINAL_PROMPT="0",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_LFS_SKIP_SMUDGE="1",
        )

    def git(self, *args, data=None, limit=None, timeout=1200, no_lazy=False):
        command = [
            "git",
            "-c",
            "core.hooksPath=",
            "-c",
            "credential.helper=",
            "-c",
            "credential.interactive=false",
            "-c",
            "http.version=HTTP/1.1",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "protocol.file.allow=" + ("always" if self.local else "never"),
            "-C",
            str(self.repo),
            *args,
        ]
        env = {**self.env, **({"GIT_NO_LAZY_FETCH": "1"} if no_lazy else {})}
        try:
            run = subprocess.run(
                command,
                input=data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ValueError(
                "Git unavailable or timed out; preserve the cache and retry"
            ) from e
        if run.returncode:
            # Do not echo credential-bearing Git/global-proxy diagnostics.
            raise ValueError(
                "Git command failed (" + args[0] + "); check network/cache and retry"
            )
        if limit is not None and len(run.stdout) > limit:
            raise ValueError("Git response exceeds resource budget")
        return run.stdout

    def initialize(self):
        if any(
            p.is_symlink() or p.is_junction() for p in (self.path, *self.path.parents)
        ):
            raise ValueError("Cache path must not contain symlinks")
        marker = self.path / "CARD-IMAGE-CACHE.json"
        expected = {
            "schema": 1,
            "repository": self.repository,
            "purpose": "artwork-only",
        }
        if self.path.exists():
            if not any(self.path.iterdir()):
                write(marker, expected)
            if not marker.is_file() or read(marker) != expected:
                raise ValueError(
                    "Choose an empty/new tool cache; existing directory is not owned by this tool"
                )
        else:
            self.path.mkdir(parents=True)
            write(marker, expected)
        if (
            self.repo.is_symlink()
            or self.repo.is_junction()
            or (self.repo / ".git").is_symlink()
        ):
            raise ValueError("Git cache must not be a symlink")
        if not (self.repo / ".git").exists():
            self.repo.mkdir(exist_ok=True)
            if any(self.repo.iterdir()):
                raise ValueError("Git repository cache is unexpectedly nonempty")
            self.git("init")
            self.git("remote", "add", "origin", self.repository)
            if not self.local:
                self.git("config", "remote.origin.promisor", "true")
                self.git("config", "remote.origin.partialclonefilter", "blob:none")
        if self.git("remote", "get-url", "origin").decode().strip() != self.repository:
            raise ValueError("Cache origin mismatch; not changed")

    def fetch(self, commit=None):
        if commit is not None and not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("--commit requires a full 40-character Git commit")
        self.initialize()
        if commit:
            present = (
                self.git(
                    "cat-file",
                    "--batch-check=%(objecttype)",
                    data=(commit + "\n").encode(),
                    no_lazy=True,
                )
                .decode()
                .strip()
            )
            if present == "commit":
                return commit
        self.git(
            "fetch",
            "--no-tags",
            "--depth=1",
            "--recurse-submodules=no",
            *([] if self.local else ["--filter=blob:none"]),
            "origin",
            commit or "refs/heads/main",
        )
        actual = self.git("rev-parse", "FETCH_HEAD^{commit}").decode().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", actual) or (commit and actual != commit):
            raise ValueError("Git commit identity mismatch")
        return actual

    def tree(self, commit):
        result = {}
        for row in self.git("ls-tree", "-r", "-z", commit).split(b"\0"):
            if not row:
                continue
            head, path = row.split(b"\t", 1)
            mode, kind, key = head.decode().split()
            path = path.decode("utf-8")
            if mode in {"100644", "100755"} and kind == "blob":
                result[regular_path(path)] = key
        return result

    def prefetch(self, keys):
        keys = sorted(set(keys))
        if not all(re.fullmatch(r"[0-9a-f]{40}", k) for k in keys):
            raise ValueError("Invalid Git blob identity")
        if self.local or not keys:
            return
        lines = (
            self.git(
                "cat-file",
                "--batch-check=%(objectname) %(objecttype)",
                data=("\n".join(keys) + "\n").encode(),
                no_lazy=True,
            )
            .decode()
            .splitlines()
        )
        if len(lines) != len(keys):
            raise ValueError("Git cache response is incomplete")
        missing = []
        for key, line in zip(keys, lines):
            if line == key + " missing":
                missing.append(key)
            elif line != key + " blob":
                raise ValueError("Expected a Git blob")
        keys = missing
        if not keys:
            return
        # Batch the promisor requests. No working-tree checkout, Git LFS filter,
        # submodule, upstream executable, HTTP API, or App endpoint is invoked.
        self.git(
            "-c",
            "fetch.negotiationAlgorithm=noop",
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "--recurse-submodules=no",
            "--filter=blob:none",
            "--stdin",
            "origin",
            data=("\n".join(keys) + "\n").encode(),
        )

    def blob(self, key, limit):
        size = int(self.git("cat-file", "-s", key, no_lazy=True).decode())
        if not 0 < size <= limit:
            raise ValueError("Upstream blob is empty or exceeds size budget")
        data = self.git("cat-file", "blob", key, limit=limit, no_lazy=True)
        if (
            len(data) != size
            or sha1(b"blob " + str(size).encode() + b"\0" + data).hexdigest() != key
        ):
            raise ValueError("Git blob content hash mismatch")
        return data


def plan(
    catalog, upstream, tree, products=(), printing_ids=(), limit=None, cosmetic=()
):
    index = defaultdict(list)
    for product in upstream["collections"]:
        code = product.get("commodityCode")
        for row in product["cards"]:
            d = row.get("details", {})
            cid = str(row.get("id", ""))
            if (
                not cid.isdecimal()
                or str(d.get("id")) != cid
                or row.get("name") != d.get("cardName")
            ):
                continue
            path = row.get("image")
            if not path:
                continue
            regular_path(path)
            if path not in tree or PurePosixPath(path).suffix.lower() not in {
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
            }:
                continue
            face = sha256(
                encoded({k: v for k, v in d.items() if k not in cosmetic})
            ).hexdigest()
            key = (
                compact(code),
                number(d.get("collectionNumber")),
                compact(d.get("cardName")),
            )
            index[key].append(
                {
                    "upstreamId": cid,
                    "path": path,
                    "blob": tree[path],
                    "face": face,
                    "hp": d.get("hp"),
                    "printedNumber": d.get("collectionNumber"),
                    "finish": d.get("special_shiny_type"),
                }
            )
    wanted = set(printing_ids)
    known = {c["printingId"] for c in catalog["cards"]}
    if wanted - known:
        raise ValueError(
            "Requested printing IDs do not exist in the target App catalog"
        )
    selected = []
    skipped = []
    products = {compact(p) for p in products}
    targets = [
        c
        for c in catalog["cards"]
        if (not wanted or c["printingId"] in wanted)
        and (
            not products
            or products
            & {compact(c.get("productCode")), *map(compact, c.get("productIds", []))}
        )
    ]
    targets.sort(key=lambda c: c["printingId"])
    if limit is not None:
        targets = targets[:limit]
    if not targets:
        raise ValueError(
            "No cards selected in the target App catalog"
        )
    for c in targets:
        key = (
            compact(c.get("productCode")),
            number(c.get("collectorNumber")),
            compact(c.get("cnName")),
        )
        candidates = index.get(key, []) if all(key) else []
        full_number = compact(c.get("printedNumber") or c.get("numberLabel"))
        if "/" in full_number:
            candidates = [
                r for r in candidates if compact(r["printedNumber"]) == full_number
            ]
        if c.get("hp"):
            candidates = [r for r in candidates if str(r["hp"]) == str(c["hp"])]
        known_ids = {str(v.get("upstreamId")) for v in c.get("variants", [])}
        retained = [r for r in candidates if r["upstreamId"] in known_ids]
        if retained:
            candidates = retained
        reason = "no-exact-product-number-name-match"
        if len({r["face"] for r in candidates}) > 1:
            candidates = []
            reason = "ambiguous-printed-face"
        if not candidates:
            skipped.append(
                {
                    "printingId": c["printingId"],
                    "name": c.get("cnName"),
                    "reason": reason,
                }
            )
            continue
        chosen = min(
            candidates,
            key=lambda r: (
                0 if r["finish"] in (0, "0") else 1 if r["finish"] is None else 2,
                int(r["upstreamId"]),
                r["path"],
            ),
        )
        selected.append(
            {
                "printingId": c["printingId"],
                "engineId": c["engineId"],
                "name": c["cnName"],
                **{k: chosen[k] for k in ("upstreamId", "path", "blob")},
            }
        )
    return {"selected": selected, "skipped": skipped, "targetCards": len(targets)}
