"""M0 acceptance criterion 10: the domain layer must stay pure.

Nothing under src/career_agent/domain/ may import a database driver, an HTTP
client, an LLM SDK, or any sibling package. This is not style policing -- it is
the single property that lets the same decision logic survive the move to
PostgreSQL and a web frontend without being rewritten.

The check parses each module's syntax tree rather than importing it, so a
forbidden import is caught even if the code path is never executed.
"""

import ast
from pathlib import Path

import pytest

DOMAIN_DIR = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "domain"

FORBIDDEN_ROOTS = {
    # I/O and external services
    "sqlite3",
    "httpx",
    "requests",
    "anthropic",
    "openai",
    "yaml",
    # sibling packages: the domain must not know how it is stored or fetched
    "career_agent.storage",
    "career_agent.providers",
    "career_agent.llm",
    "career_agent.pipeline",
    "career_agent.net",
    "career_agent.render",
    "career_agent.config",
}


def _imported_modules(source: str) -> set[str]:
    """Every module name a file imports, from both `import x` and `from x import y`."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _is_forbidden(module: str) -> bool:
    return any(module == root or module.startswith(root + ".") for root in FORBIDDEN_ROOTS)


def test_domain_directory_exists() -> None:
    assert DOMAIN_DIR.is_dir(), f"expected a domain package at {DOMAIN_DIR}"


@pytest.mark.parametrize(
    "module_path",
    sorted(DOMAIN_DIR.rglob("*.py")),
    ids=lambda p: p.name,
)
def test_domain_module_imports_nothing_forbidden(module_path: Path) -> None:
    offenders = sorted(
        name
        for name in _imported_modules(module_path.read_text(encoding="utf-8"))
        if _is_forbidden(name)
    )
    assert not offenders, (
        f"{module_path.name} imports {offenders}. The domain layer must stay free of "
        "I/O and of sibling packages; move this logic into pipeline/ or storage/."
    )
