"""Fail closed when portable source grows forbidden APIs or dependencies."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Final

_FORBIDDEN_IDENTIFIERS: Final = frozenset(
    {"delete_objects", "delete_object", "mirror", "secret_cli"}
)
_FORBIDDEN_IMPORT_PARTS: Final = ("lab" + "_clip", "pipeline.core", "pipeline.research")
_PROGRAM_AND_ONE_ARGUMENT: Final = 2


def scan(root: Path) -> tuple[str, ...]:
    """Return stable file/line findings for forbidden portable-source capabilities."""
    findings: list[str] = []
    scanner = Path(__file__).resolve()
    for path in sorted(root.rglob("*.py")):
        if path.resolve() == scanner:
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as error:
            findings.append(f"{path}:{error.lineno}:syntax_error")
            continue
        for node in ast.walk(tree):
            name: str | None = None
            line: int | None = None
            if isinstance(node, ast.Name):
                name = node.id
                line = node.lineno
            elif isinstance(node, ast.Attribute):
                name = node.attr
                line = node.lineno
            if name in _FORBIDDEN_IDENTIFIERS:
                findings.append(f"{path}:{line}:forbidden_api:{name}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imported = ast.unparse(node)
                if any(part in imported for part in _FORBIDDEN_IMPORT_PARTS):
                    findings.append(f"{path}:{node.lineno}:forbidden_import")
    return tuple(findings)


def main() -> int:
    """Run the scanner as a module and return nonzero for every finding."""
    root = Path(sys.argv[1]) if len(sys.argv) == _PROGRAM_AND_ONE_ARGUMENT else Path("src")
    findings = scan(root)
    if findings:
        _ = sys.stdout.write("\n".join(findings) + "\n")
        return 1
    _ = sys.stdout.write("forbidden-api scan passed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
