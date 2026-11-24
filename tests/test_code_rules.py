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


TESTS = Path(__file__).resolve().parent


def _attribute_text(node: ast.AST) -> str:
    """'os.name' for os.name, 'sys.platform' for sys.platform, else ''."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return ""


def test_the_app_tells_systems_apart_by_sys_platform_only():
    """One way to ask which system this is (sys.platform), so a test that fakes another system changes one
    thing and is sure the code sees it; mypy also understands sys.platform checks, and not os.name."""
    found = []
    for path in sorted(WICK.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if _attribute_text(node) == "os.name":
                found.append(f"{path.relative_to(WICK.parent)}:{node.lineno}")
    assert not found, "use sys.platform:\n" + "\n".join(found)


def test_tests_fake_another_system_through_sys_platform():
    """Changing os.name in a test does nothing (the app reads sys.platform), so the test would quietly run the
    real system's code: on Windows, that once meant a real message box and a test that never finished."""
    found = []
    for path in sorted(TESTS.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            # monkeypatch.setattr(os, "name", ...) / setattr(os, "name", ...)
            if (isinstance(first, ast.Name) and first.id == "os" and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant) and node.args[1].value == "name"):
                found.append(f"{path.relative_to(TESTS.parent)}:{node.lineno}")
            # monkeypatch.setattr("os.name", ...) / patch("os.name", ...)
            if isinstance(first, ast.Constant) and first.value == "os.name":
                found.append(f"{path.relative_to(TESTS.parent)}:{node.lineno}")
    assert not found, "patch sys.platform instead:\n" + "\n".join(found)
