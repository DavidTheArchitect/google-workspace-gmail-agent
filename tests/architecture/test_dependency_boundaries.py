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


def _imported_module(node: ast.AST) -> str | None:
    if isinstance(node, ast.ImportFrom):
        return node.module
    if isinstance(node, ast.Import) and node.names:
        return node.names[0].name
    return None
