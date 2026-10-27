"""Shared, deny-first policy for public source and generated source archives."""

from pathlib import Path, PurePosixPath
import os
import re

ROOT = Path(__file__).resolve().parents[2]
FOLDERS = (
    "apps/api",
    "apps/web/src",
    "apps/web/e2e",
    "apps/desktop-api",
    "apps/desktop-web",
    "apps/online-server",
    "apps/native-shell",
    "packages",
    "scripts",
    "tests",
    "docs",
    "licenses",
    "rulesets",
    "resources/schemas",
    "resources/examples",
    "ops/online",
    "benchmarks/online",
    ".github/workflows",
)
FILES = (
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    ".gitignore",
    ".gitattributes",
    "start.sh",
    "start.ps1",
    "RELEASE.json",
    "package.json",
    "package-lock.json",
    "apps/native-shell/src-tauri/icons/icon.png",
    "releases/projects.json",
    "releases/migration-status.json",
    "releases/p4d-acceptance.json",
    "releases/p5d-acceptance.json",
    "releases/p5d-user-acceptance.json",
    "releases/p55-user-acceptance.json",
    "releases/p6a-baseline.json",
    "releases/p6b-streams.json",
    "releases/p6c-scheduler.json",
    "releases/p6d-workers.json",
    "releases/p6e-budgets.json",
    "releases/p7a-isolation.json",
    "releases/p7b-matrix.json",
    "releases/p7b-before-driver-repair.json",
    "releases/p7-measured-p6e-baseline.json",
    "releases/source-candidates.json",
    "ops/online/Dockerfile.dockerignore",
    "benchmarks/online/Dockerfile.dockerignore",
)
EXCLUDED = {
    ".git",
    ".catalog",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    "test-results",
    "playwright-report",
    "fixtures",
    "var",
    "data",
    "runtime",
    "vendor",
    "artifacts",
    "outputs",
    "exports",
    "dist",
    "build",
    "target",
    "gen",
    "secrets",
    "credentials",
    "private",
    ".aws",
}
ALLOWED = {
    ".js",
    ".py",
    ".ts",
    ".tsx",
    ".css",
    ".md",
    ".sh",
    ".ps1",
    ".txt",
    ".in",
    ".lock",
    ".json",
    ".html",
    ".mjs",
    ".cjs",
    ".svg",
    ".toml",
    ".yaml",
    ".yml",
    ".sql",
    ".rs",
}
SYNTHETIC_JSON = frozenset({"tests/synthetic/protocol/scaffold.json", "tests/synthetic/protocol/v2.json"})
PATTERNS = {
    "credential_pattern": rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
    "machine_path": rb"(?:/Users/[A-Za-z0-9_-]+/|/Volumes/[A-Za-z0-9_-]+/|[A-Z]:[\\/](?:Users|Work)[\\/])",
}


def safe_name(name):
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
        and ":" not in name
        and path.as_posix() == name
    )


def private_name(name):
    if not safe_name(name):
        return True
    p = PurePosixPath(name)
    return (
        bool(EXCLUDED.intersection(part.lower() for part in p.parts))
        or any(part.endswith(".egg-info") for part in p.parts)
        or (p.name.startswith(".env") and p.name not in {".env.example", ".env.sample"})
        or p.suffix.lower()
        in {".sqlite", ".db", ".pem", ".key", ".p12", ".pfx", ".pyc", ".log"}
        or p.name.endswith((".sqlite-wal", ".sqlite-shm", ".tsbuildinfo"))
        or p.name.lower()
        in {"credentials.json", "secrets.json", "password.txt", "token.json"}
        or p.name
        in {
            "MIGRATION.json",
            "TRANSFER-MANIFEST.json",
            "PUBLIC-SOURCE.json",
            "RELEASE-FILES.json",
            "db_password",
        }
    )


def allowed_source(name):
    if private_name(name):
        return False
    p = PurePosixPath(name)
    if name.startswith("tests/") and p.suffix == ".json":
        return name in SYNTHETIC_JSON
    in_scope = name in FILES or any(name.startswith(f + "/") for f in FOLDERS)
    if p.parent.as_posix() == "apps/web":
        in_scope = True
    if not in_scope:
        return False
    if p.name in {
        "LICENSE",
        "NOTICE",
        "Dockerfile",
        ".dockerignore",
        ".env.example",
        ".env.sample",
    }:
        return True
    return name in FILES or p.suffix in ALLOWED


def safe_file(root, name):
    root = Path(root).resolve()
    if not safe_name(name):
        raise ValueError("Unsafe source path")
    p = root / name
    if any(
        part.is_symlink()
        for part in [p, *p.parents]
        if part != root and part.is_relative_to(root)
    ):
        raise ValueError("Symlink not permitted: " + name)
    if not p.is_file() or not p.resolve().is_relative_to(root):
        raise ValueError("Missing or external source file: " + name)
    return p


def source_files(root=ROOT):
    root = Path(root).resolve()
    names = {n for n in FILES if (root / n).exists() or (root / n).is_symlink()}
    for folder in (*FOLDERS, "apps/web"):
        start = root / folder
        if start.is_symlink():
            raise ValueError("Symlink not permitted: " + folder)
        if not start.exists():
            continue
        for current, dirs, files in os.walk(start, followlinks=False):
            for child in dirs:
                path = Path(current) / child
                if path.is_symlink() and child not in EXCLUDED:
                    raise ValueError(
                        "Symlink not permitted: " + path.relative_to(root).as_posix()
                    )
            dirs[:] = [
                d for d in dirs if d not in EXCLUDED and not d.endswith(".egg-info")
            ]
            if folder == "apps/web":
                dirs[:] = []
            for filename in files:
                name = (Path(current) / filename).relative_to(root).as_posix()
                if allowed_source(name):
                    safe_file(root, name)
                    names.add(name)
    return sorted(n for n in names if allowed_source(n))


def inspect(name, content):
    found = [key for key, pattern in PATTERNS.items() if re.search(pattern, content)]
    if private_name(name):
        found.append("private_or_generated_path")
    return found
