# ruff: noqa: E402
"""Enforce registered package dependencies, publication and migration status."""

import argparse
import ast
import json
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.public.policy import source_files, safe_file


def check(root=ROOT):
    root = Path(root).resolve()
    registry = json.loads((root / "releases/projects.json").read_text())
    projects = registry["projects"]
    errors = []
    export = set(source_files(root))
    python_modules = {p["module"]: p for p in projects if p["language"] == "python"}
    names = [p["distribution"] for p in projects]
    if len(names) != len(set(names)):
        errors.append("duplicate distribution")
    for project in projects:
        base = root / project["path"]
        protocol = project["distribution"] in {
            "ptcg-battle-protocol",
            "@ptcg/battle-protocol",
        }
        provider = project["distribution"] == "ptcg-engine-provider"
        core = project["distribution"] == "ptcg-battle-core"
        online = project["distribution"] == "ptcg-online-server"
        gui = project["distribution"] in {"@ptcg/battle-ui", "@ptcg/desktop-web"}
        expected_stage = (
            ("P3-C" if project["distribution"] == "@ptcg/desktop-web" else "P3-B")
            if gui
            else (
                "P4-C"
                if online
                else "P2-D" if (protocol or core) else "P2-B" if provider else "P1"
            )
        )
        if (
            project["stage"] != expected_stage
            or project["implementationReady"] is not False
        ):
            errors.append(project["path"] + ": scaffold status drift")
        if project["language"] == "python":
            metadata = tomllib.loads(
                safe_file(root, project["path"] + "/pyproject.toml").read_text()
            )
            if metadata["project"]["name"] != project["distribution"]:
                errors.append(project["path"] + ": distribution mismatch")
            if (
                metadata["project"].get("dependencies", [])
                != project["allowedRuntimeRequirements"]
            ):
                errors.append(
                    project["path"] + ": runtime requirements differ from contract"
                )
            if (
                not (protocol or core or online)
                and project["allowedRuntimeRequirements"]
            ):
                errors.append(
                    project["path"] + ": inactive scaffold gained runtime requirements"
                )
            for file in sorted((base / "src").rglob("*.py")):
                rel = file.relative_to(root).as_posix()
                tree = ast.parse(safe_file(root, rel).read_text())
                for node in ast.walk(tree):
                    imports = []
                    if isinstance(node, ast.Import):
                        imports = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and not node.level:
                        imports = [node.module or ""]
                    elif isinstance(node, ast.Call):
                        # Dynamic imports and import-path modification bypass static contracts.
                        name = ast.unparse(node.func)
                        if name in {
                            "__import__",
                            "eval",
                            "exec",
                            "importlib.import_module",
                            "sys.path.insert",
                            "sys.path.append",
                        }:
                            audited = {
                                "packages/battle-core/src/ptcg_core/engine_loader.py": {
                                    "exec"
                                },
                                "packages/battle-core/src/ptcg_core/engine_registry.py": {
                                    "importlib.import_module"
                                },
                                "packages/battle-core/src/ptcg_core/compat.py": {
                                    "importlib.import_module"
                                },
                            }
                            if name not in audited.get(rel, set()):
                                errors.append(rel + ": dynamic import/path escape")
                    for name in imports:
                        top = name.split(".")[0]
                        allowed = {
                            project["module"],
                            *project["allowedInternalImports"],
                            *project["allowedExternalImports"],
                        }
                        if top not in allowed or (
                            (protocol or provider)
                            and top
                            in {
                                "apps",
                                "packages",
                                "ptcg",
                                "fastapi",
                                "sqlite3",
                                "torch",
                            }
                        ):
                            errors.append(rel + ": forbidden import " + top)
                        if (
                            top in python_modules
                            and top != project["module"]
                            and top not in project["allowedInternalImports"]
                        ):
                            errors.append(rel + ": undeclared project dependency")
                if file.name == "__init__.py":
                    # P1 imports must be inert: only docstrings and constant metadata.
                    for node in tree.body:
                        if isinstance(node, ast.Expr) and isinstance(
                            node.value, ast.Constant
                        ):
                            continue
                        if isinstance(node, ast.Assign) and isinstance(
                            node.value, ast.Constant
                        ):
                            continue
                        errors.append(rel + ": non-inert scaffold initialization")
                if rel not in export:
                    errors.append(rel + ": not exported")
        else:
            metadata = json.loads(
                safe_file(root, project["path"] + "/package.json").read_text()
            )
            if (
                metadata["name"] != project["distribution"]
                or metadata.get("private") is not True
            ):
                errors.append(
                    project["path"] + ": JS package identity/private flag mismatch"
                )
            for field, contract in (
                ("dependencies", "allowedPackageDependencies"),
                ("peerDependencies", "allowedPeerDependencies"),
                ("scripts", "allowedScripts"),
            ):
                if metadata.get(field, {}) != project.get(contract, {}):
                    errors.append(
                        project["path"]
                        + ": JS dependencies/scripts differ from contract"
                    )
            for file in (base / "src").rglob("*"):
                if file.is_file() and file.suffix in {".js", ".ts", ".tsx"}:
                    rel = file.relative_to(root).as_posix()
                    content = safe_file(root, rel).read_text()
                    refs = re.findall(
                        r"""(?:from\s*|import\s*(?:\(\s*)?|require\s*\(\s*)['"]([^'"]+)""",
                        content,
                    )
                    allowed_refs = set(
                        project["allowedInternalImports"]
                        + project["allowedExternalImports"]
                    )
                    if any(
                        (
                            not (file.parent / ref)
                            .resolve()
                            .is_relative_to(base.resolve())
                            if ref.startswith(".")
                            else ref not in allowed_refs
                        )
                        for ref in refs
                    ):
                        errors.append(rel + ": cross-package or external JS import")
                    capabilities = set(
                        re.findall(
                            r"\b(fetch|WebSocket|XMLHttpRequest|localStorage|sessionStorage|sendBeacon)\b",
                            content,
                        )
                    )
                    audited_capabilities = {
                        "apps/desktop-web/src/practice/transport.ts": {"fetch"},
                        "apps/desktop-web/src/practice/storage.ts": {
                            "localStorage",
                            "sessionStorage",
                        },
                    }
                    if capabilities - audited_capabilities.get(rel, set()):
                        errors.append(
                            rel + ": networking/storage outside audited mode adapter"
                        )
                    if rel not in export:
                        errors.append(rel + ": not exported")
        for filename in [
            "LICENSE",
            "README.md",
            "pyproject.toml" if project["language"] == "python" else "package.json",
        ]:
            name = project["path"] + "/" + filename
            if name not in export:
                errors.append(name + ": metadata/license not exported")
    status = json.loads((root / "releases/migration-status.json").read_text())
    if (
        status.get("currentPhase") != "P4"
        or status.get("productionRoutingChanged") is not False
    ):
        errors.append("migration phase/routing marker drift")
    phase = status["phases"].get("P2")
    if phase not in {"in_progress", "completed"} or (
        phase == "completed"
        and (
            status.get("subphases", {}).get("P2-D") != "completed"
            or not (root / "releases/p2d-acceptance.json").is_file()
        )
    ):
        errors.append("P2 implementation misrepresented as complete")
    p3_phase = status["phases"].get("P3")
    if p3_phase not in {"in_progress", "completed"} or (
        p3_phase == "completed"
        and (
            any(
                status.get("subphases", {}).get(p) != "completed"
                for p in ("P3-A", "P3-B", "P3-C")
            )
            or status.get("p3cAcceptanceDocument") != "docs/P3C完整产品接入验收.md"
            or not (root / "docs/P3C完整产品接入验收.md").is_file()
        )
    ):
        errors.append("P3 implementation misrepresented as complete")
    if status["phases"].get("P4") != "in_progress":
        errors.append("P4 implementation misrepresented as complete")
    for phase in range(5, 9):
        if status["phases"].get("P" + str(phase)) != "planned":
            errors.append("future implementation misrepresented as complete")
    for name in ["docs/产品化重构设计.md", "docs/产品化重构分阶段实施.md"]:
        text = (root / name).read_text()
        if "<!-- migration-phase: P4; production-routing: legacy -->" not in text:
            errors.append(name + ": missing phase status")
    return sorted(set(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    errors = check()
    print(json.dumps({"boundaryErrors": errors}, ensure_ascii=False))
    raise SystemExit(bool(errors))
