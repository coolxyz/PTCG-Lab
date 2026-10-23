"""Verified resource snapshots. This module never imports or executes engine code."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path, PurePosixPath
import re
import stat
from types import MappingProxyType
from typing import Protocol

from .catalog import CARD_FIELDS, MemoryCatalog
from .values import (
    JsonSnapshot,
    ProviderError,
    canonical,
    is_digest,
    parse_json,
    require,
    sha256,
)

ABI = "ptcg-rules-input-v1"
SINGLE_ROLES = frozenset({"catalog", "format", "scope", "effects", "mapping", "specs"})
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024


def safe_name(name):
    if not isinstance(name, str) or name in {"", "."} or len(name) > 240:
        return False
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and path.as_posix() == name
        and not any(
            part in {".", ".."} or part.endswith((".", " ")) for part in path.parts
        )
        and not any(c in name for c in '\\:\x00<>"|?*')
        and not any(
            part.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *[f"COM{i}" for i in range(1, 10)],
                *[f"LPT{i}" for i in range(1, 10)],
            }
            for part in path.parts
        )
        and all(ord(c) >= 32 for c in name)
    )


def read_asset(root, name, limit=MAX_FILE_BYTES):
    """Read only explicit regular files; pin hashes separately before use.

    Caller owns the trusted, read-only asset directory. No archive extraction,
    writable cache, network lookup, fallback directory or interpreter path edit.
    """
    require(safe_name(name), "UNSAFE_ASSET_PATH")
    root = Path(root)
    try:
        require(not root.is_symlink(), "SYMLINK_ASSET")
        root = root.resolve(strict=True)
        candidate = root / name
        for part in (candidate, *candidate.parents):
            if part == root:
                break
            require(not part.is_symlink(), "SYMLINK_ASSET")
        require(
            candidate.resolve(strict=True).is_relative_to(root), "UNSAFE_ASSET_PATH"
        )
        before = candidate.stat()
        require(stat.S_ISREG(before.st_mode), "NON_REGULAR_ASSET")
        require(before.st_size <= limit, "ASSET_TOO_LARGE")
        with candidate.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            require(
                (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                "ASSET_CHANGED",
            )
            content = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        require(len(content) <= limit, "ASSET_TOO_LARGE")
        require(
            (opened.st_size, opened.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
            and len(content) == after.st_size,
            "ASSET_CHANGED",
        )
        return content
    except ProviderError:
        raise
    except (OSError, ValueError):
        raise ProviderError("ASSET_UNAVAILABLE") from None


@dataclass(frozen=True, slots=True)
class Asset:
    name: str
    role: str
    digest: str
    content: bytes


@dataclass(frozen=True, slots=True)
class SupportResult:
    supported: bool
    missing: tuple[str, ...]
    version: str


@dataclass(frozen=True, slots=True)
class RuleBundle:
    bundle_id: str
    manifest_digest: str
    upstream_commit: str
    assets: Mapping
    documents: Mapping
    catalog: MemoryCatalog
    _effects: Mapping

    def engine_sources(self):
        return MappingProxyType(
            {a.name: a.content for a in self.assets.values() if a.role == "engine"}
        )

    def rule_release_id(self, *, core_sha256, provider_sha256):
        """New v2 identity only. Never an alias for a legacy implementation_id."""
        require(
            is_digest(core_sha256) and is_digest(provider_sha256),
            "INVALID_ARTIFACT_DIGEST",
        )
        return "rules-v2-" + sha256(
            canonical(
                {
                    "schema": 1,
                    "bundleId": self.bundle_id,
                    "coreArtifactSha256": core_sha256,
                    "providerArtifactSha256": provider_sha256,
                }
            )
        )

    def scope_support(self, entries):
        scope = self.documents["scope"]
        missing = []
        for e in self.catalog.normalize(entries):
            card = self.catalog.card(e.printing_id)
            if not (
                card["mark"] in scope.value["allowedMarks"]
                or (
                    card["category"] == "能量"
                    and card["basicEnergyType"] in scope.value["basicEnergyTypes"]
                )
            ):
                missing.append(e.printing_id)
        return SupportResult(not missing, tuple(missing), scope.digest)

    def effect_support(self, entries):
        missing = []
        for e in self.catalog.normalize(entries):
            card = self.catalog.card(e.printing_id)
            effect = self._effects.get(card["engineId"])
            if (
                not effect
                or effect["status"] != "experimental"
                or e.printing_id not in effect["printings"]
                or card["effectStatus"] != "verified"
            ):
                missing.append(e.printing_id)
        return SupportResult(
            not missing, tuple(missing), self.documents["effects"].digest
        )


class EngineProvider(Protocol):
    """Prepare verified inputs for a future core factory, without activating it."""

    def load(self) -> RuleBundle: ...


@dataclass(frozen=True, slots=True)
class DirectoryEngineProvider:
    root: Path
    expected_manifest_sha256: str

    def load(self):
        require(is_digest(self.expected_manifest_sha256), "INVALID_MANIFEST_PIN")
        raw = read_asset(self.root, "bundle.json", MAX_MANIFEST_BYTES)
        require(sha256(raw) == self.expected_manifest_sha256, "MANIFEST_MISMATCH")
        manifest = parse_json(raw)
        require(
            isinstance(manifest, dict)
            and set(manifest)
            == {"schema", "engineAbi", "upstreamCommit", "components"},
            "INVALID_MANIFEST",
        )
        require(
            type(manifest["schema"]) is int
            and manifest["schema"] == 1
            and manifest["engineAbi"] == ABI,
            "UNSUPPORTED_BUNDLE",
        )
        require(
            isinstance(manifest["upstreamCommit"], str)
            and re.fullmatch(r"[0-9a-f]{40}", manifest["upstreamCommit"]),
            "INVALID_MANIFEST",
        )
        rows = manifest["components"]
        require(isinstance(rows, list) and 1 <= len(rows) <= 4096, "INVALID_MANIFEST")
        names, paths, single = set(), set(), set()
        total = 0
        # Validate the entire plan before reading any component.
        for row in rows:
            require(
                isinstance(row, dict)
                and set(row) == {"name", "role", "path", "sha256", "size"},
                "INVALID_MANIFEST",
            )
            require(
                safe_name(row["name"]) and safe_name(row["path"]), "UNSAFE_ASSET_PATH"
            )
            require(
                row["name"].casefold() not in names
                and row["path"].casefold() not in paths,
                "DUPLICATE_ASSET",
            )
            names.add(row["name"].casefold())
            paths.add(row["path"].casefold())
            role = row["role"]
            require(
                isinstance(role, str) and role in SINGLE_ROLES | {"engine", "license"},
                "INVALID_ASSET_ROLE",
            )
            if role in SINGLE_ROLES:
                require(role not in single, "DUPLICATE_ASSET_ROLE")
                single.add(role)
                require(row["name"].endswith(".json"), "INVALID_ASSET_KIND")
            elif role == "engine":
                require(
                    row["name"].startswith("ptcg/") and row["name"].endswith(".py"),
                    "INVALID_ASSET_KIND",
                )
            require(is_digest(row["sha256"]), "INVALID_ASSET_DIGEST")
            require(
                type(row["size"]) is int and 0 <= row["size"] <= MAX_FILE_BYTES,
                "ASSET_TOO_LARGE",
            )
            total += row["size"]
        require(total <= MAX_BUNDLE_BYTES, "BUNDLE_TOO_LARGE")
        require(
            single == SINGLE_ROLES
            and any(r["role"] == "license" for r in rows)
            and any(
                r["role"] == "engine" and r["name"] == "ptcg/__init__.py" for r in rows
            ),
            "INCOMPLETE_BUNDLE",
        )
        assets, docs = {}, {}
        for row in rows:
            content = read_asset(self.root, row["path"], row["size"])
            require(
                len(content) == row["size"] and sha256(content) == row["sha256"],
                "ASSET_HASH_MISMATCH",
            )
            assets[row["name"]] = Asset(
                row["name"], row["role"], row["sha256"], content
            )
            if row["role"] in SINGLE_ROLES:
                docs[row["role"]] = JsonSnapshot.from_bytes(content)
        catalog = MemoryCatalog(docs["catalog"])
        policy = docs["format"].value
        require(
            isinstance(policy.get("id"), str)
            and isinstance(policy.get("effectiveAt"), str),
            "INVALID_FORMAT",
        )
        try:
            date.fromisoformat(policy["effectiveAt"])
            date.fromisoformat(catalog.document.value["asOf"])
        except (TypeError, ValueError):
            raise ProviderError("INVALID_FORMAT_DATE") from None
        require(
            catalog.document.value["formatId"] == policy["id"],
            "CATALOG_FORMAT_MISMATCH",
        )
        for key in ("allowedMarks", "basicEnergyTypes", "legacyReprintNames"):
            require(
                isinstance(policy.get(key), tuple)
                and all(isinstance(v, str) for v in policy[key]),
                "INVALID_FORMAT",
            )
        for key in ("deckSize", "sameNameLimit", "aceSpecLimit"):
            require(
                type(policy.get(key)) is int
                and policy[key] >= (0 if key == "aceSpecLimit" else 1),
                "INVALID_FORMAT",
            )
        mapping = docs["mapping"].value
        require(
            set(mapping) == {"schema", "printings"}
            and mapping["schema"] == "engine-mapping-v1"
            and isinstance(mapping["printings"], Mapping),
            "INVALID_MAPPING",
        )
        for pid, card in mapping["printings"].items():
            require(
                isinstance(card, Mapping)
                and set(card) == CARD_FIELDS
                and card["printingId"] == pid,
                "INVALID_MAPPING",
            )
        require(isinstance(docs["specs"].value.get("cards"), tuple), "INVALID_SPECS")
        scope = docs["scope"].value
        require(
            all(
                isinstance(scope.get(k), tuple)
                and all(isinstance(v, str) for v in scope[k])
                for k in ("allowedMarks", "basicEnergyTypes")
            ),
            "INVALID_SCOPE",
        )
        effects = {}
        require(
            isinstance(docs["effects"].value.get("effects"), tuple), "INVALID_EFFECTS"
        )
        for effect in docs["effects"].value["effects"]:
            require(
                isinstance(effect, Mapping)
                and isinstance(effect.get("effectKey"), str)
                and isinstance(effect.get("status"), str)
                and isinstance(effect.get("printings"), tuple)
                and all(isinstance(p, str) for p in effect["printings"]),
                "INVALID_EFFECTS",
            )
            require(effect["effectKey"] not in effects, "DUPLICATE_EFFECT")
            effects[effect["effectKey"]] = effect
        logical = [
            {k: row[k] for k in ("name", "role", "sha256", "size")} for row in rows
        ]
        identity = canonical(
            {
                "schema": 1,
                "engineAbi": ABI,
                "upstreamCommit": manifest["upstreamCommit"],
                "components": sorted(logical, key=lambda r: r["name"]),
            }
        )
        return RuleBundle(
            "bundle-v1-" + sha256(identity),
            sha256(raw),
            manifest["upstreamCommit"],
            MappingProxyType(assets),
            MappingProxyType(docs),
            catalog,
            MappingProxyType(effects),
        )
