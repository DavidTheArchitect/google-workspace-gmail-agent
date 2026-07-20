"""Prevent infrastructure imports from crossing the deterministic-core boundary."""

import ast
from pathlib import Path

FORBIDDEN_ROOTS = {
    "agent_framework",
    "argparse",
    "openai",
    "playwright",
    "pydantic_settings",
}
FORBIDDEN_INTERNAL_DEPENDENCIES = {
    "browser": frozenset(
        {
            "application",
            "cli",
            "composition",
            "console",
            "launcher",
            "llm",
            "reflex_console",
            "workflow",
        }
    ),
    "domain": frozenset(
        {
            "application",
            "audit",
            "browser",
            "cli",
            "composition",
            "console",
            "infrastructure",
            "launcher",
            "llm",
            "reflex_console",
            "settings",
            "startup",
            "version",
            "workflow",
        }
    ),
    "llm": frozenset(
        {
            "application",
            "audit",
            "browser",
            "console",
            "infrastructure",
            "reflex_console",
            "workflow",
        }
    ),
    "schemas": frozenset(
        {
            "application",
            "audit",
            "browser",
            "cli",
            "composition",
            "console",
            "exceptions",
            "infrastructure",
            "launcher",
            "llm",
            "reflex_console",
            "settings",
            "startup",
            "version",
            "workflow",
        }
    ),
}


def test_domain_and_schemas_do_not_import_infrastructure_packages() -> None:
    source_root = Path(__file__).parents[2] / "src" / "compliance_agent"
    violations: list[str] = []
    for package in ("domain", "schemas"):
        for path in (source_root / package).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = _imported_module(node)
                if module and module.split(".", maxsplit=1)[0] in FORBIDDEN_ROOTS:
                    violations.append(f"{path.name}: {module}")

    assert violations == []


def test_internal_packages_preserve_dependency_boundaries() -> None:
    source_root = Path(__file__).parents[2] / "src" / "compliance_agent"
    violations: list[str] = []
    for package, forbidden in FORBIDDEN_INTERNAL_DEPENDENCIES.items():
        for path in (source_root / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = _imported_module(node)
                dependency = _internal_dependency(module)
                if dependency in forbidden:
                    relative_path = path.relative_to(source_root)
                    violations.append(f"{relative_path}: {module}")

    assert violations == []


def _imported_module(node: ast.AST) -> str | None:
    if isinstance(node, ast.ImportFrom):
        return node.module
    if isinstance(node, ast.Import) and node.names:
        return node.names[0].name
    return None


def _internal_dependency(module: str | None) -> str | None:
    if not module or not module.startswith("compliance_agent."):
        return None
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else None
