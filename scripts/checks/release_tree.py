"""Validate the product source tree without private assets or running services."""

from pathlib import Path
import ast
import re
import subprocess
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.public.policy import allowed_source, safe_file, source_files
from scripts.public.audit import audit_worktree
from scripts.native.contracts import check as native_check


def check(root=ROOT):
    root = Path(root)
    errors = []
    names = source_files(root)
    for name in names:
        path = safe_file(root, name)
        if path.suffix == ".py":
            try:
                tree = ast.parse(path.read_bytes(), filename=name)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("scripts."):
                        target = root.joinpath(*node.module.split("."))
                        if not target.with_suffix(".py").is_file() and not target.is_dir():
                            errors.append(name + ": missing import " + node.module)
            except SyntaxError:
                errors.append(name + ": invalid Python syntax")
        if path.suffix == ".md":
            for match in re.finditer(r"\]\(([^)]+)\)", path.read_text()):
                target = unquote(match[1].split("#")[0])
                if target and "://" not in target and not target.startswith("mailto:") and not (path.parent / target).exists():
                    errors.append(name + ": missing link " + target)
    # Include staged/untracked candidates but ignore deleted files and local caches.
    listed = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root).decode().split("\0")
    for name in set(listed) - {""}:
        if (root / name).exists() and not allowed_source(name):
            errors.append("Not product source: " + name)
    audit = audit_worktree(root)
    errors += ["Privacy finding: " + row["path"] for rows in audit.values() for row in rows]
    errors += native_check(root)
    return sorted(set(errors))


if __name__ == "__main__":
    errors = check()
    for error in errors:
        print(error)
    if errors:
        raise SystemExit(1)
    print("Product source, links, imports, native contracts and privacy checks passed.")
