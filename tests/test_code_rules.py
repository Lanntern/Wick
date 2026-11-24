"""Rules about the code itself that linters don't check."""

import ast
from pathlib import Path

WICK = Path(__file__).resolve().parent.parent / "wick"


def _module(path: Path) -> list[str]:
    parts = list(path.relative_to(WICK.parent).with_suffix("").parts)
    return parts[:-1] if parts[-1] == "__init__" else parts


def test_no_module_uses_another_modules_private_names():
    """A name starting with "_" is that module's own business. One that others need gets a public name."""
    found = []
    for path in sorted(WICK.rglob("*.py")):
        here = _module(path)
        package = here if path.name == "__init__.py" else here[:-1]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = (package[:len(package) - node.level + 1] if node.level else []) + (node.module or "").split(".")
            target = [t for t in target if t]
            for alias in node.names:
                private = alias.name.startswith("_") and not alias.name.startswith("__")
                is_module = WICK.parent.joinpath(*target, alias.name + ".py").exists()   # (a private module: fine)
                if private and not is_module:
                    found.append(f"{path.relative_to(WICK.parent)}: {'.'.join(target)}.{alias.name}")
    assert not found, "\n".join(found)
