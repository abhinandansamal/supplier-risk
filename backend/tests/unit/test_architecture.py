"""Architecture rule: api -> services -> agent -> tools -> zetaris; only tools may use zetaris."""

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

CHAIN = ["api", "services", "agent", "tools", "zetaris"]
ZETARIS = "zetaris"
ZETARIS_USERS = {"tools", "zetaris"}


def _module_parts(path: Path, root: Path) -> list[str]:
    """Return the dotted module path of a file as parts, starting with 'app'."""
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ["app", *parts]


def _imported_modules(path: Path, root: Path) -> set[str]:
    """Return absolute dotted names (normalised to start with 'app') imported by a file."""
    package = _module_parts(path, root)
    if path.name != "__init__.py":
        package = package[:-1]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                module = ".".join([*base, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            found.add(module)
            # `from app import zetaris` imports a submodule through the package name.
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return {
        name.removeprefix("backend.") for name in found if name.startswith(("app", "backend.app"))
    }


def _layer(name: str) -> str | None:
    """Return the top-level package ('tools', 'core', ...) of an 'app.x.y' name."""
    parts = name.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "app" else None


def find_violations(root: Path) -> list[str]:
    """Return human-readable layer violations for every python file under root."""
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        # Top-level modules such as main.py belong to no layer (own is None).
        own = _layer(".".join(_module_parts(path, root))) if path.parent != root else None
        seen: set[str] = set()
        for target_name in sorted(_imported_modules(path, root)):
            target = _layer(target_name)
            if target is None or target == own or target in seen:
                continue
            seen.add(target)
            where = f"{path.relative_to(root)} imports {target_name}"
            if target == ZETARIS and own not in ZETARIS_USERS:
                violations.append(f"{where}: only tools/ may import the Zetaris client")
            elif own in CHAIN and target in CHAIN and CHAIN.index(target) < CHAIN.index(own):
                violations.append(f"{where}: lower layer {own} imports higher layer {target}")
            elif own is not None and own not in CHAIN and target in CHAIN:
                violations.append(f"{where}: cross-cutting package {own} imports layer {target}")
    return violations


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)


def test_real_code_follows_the_layer_rule() -> None:
    assert find_violations(APP_ROOT) == []


def test_downward_imports_are_allowed(tmp_path: Path) -> None:
    _write(tmp_path, "api/a.py", "from app.services import x\nfrom app.core import settings\n")
    _write(tmp_path, "agent/a.py", "from app.tools import x\nfrom app.llm import y\n")
    _write(tmp_path, "tools/a.py", "from app.zetaris.client import Z\nimport app.cache\n")
    assert find_violations(tmp_path) == []


def test_flags_api_importing_zetaris(tmp_path: Path) -> None:
    _write(tmp_path, "api/a.py", "from app.zetaris.client import Z\n")
    assert "only tools/ may import" in find_violations(tmp_path)[0]


def test_flags_zetaris_import_through_package_name(tmp_path: Path) -> None:
    _write(tmp_path, "agent/a.py", "from app import zetaris\n")
    assert find_violations(tmp_path)


def test_flags_backend_prefixed_import(tmp_path: Path) -> None:
    _write(tmp_path, "services/a.py", "import backend.app.zetaris.client\n")
    assert find_violations(tmp_path)


def test_flags_relative_import_of_zetaris(tmp_path: Path) -> None:
    _write(tmp_path, "agent/a.py", "from ..zetaris import client\n")
    assert find_violations(tmp_path)


def test_flags_lower_layer_importing_higher(tmp_path: Path) -> None:
    _write(tmp_path, "tools/a.py", "from app.agent import graph\n")
    _write(tmp_path, "agent/b.py", "from app.services import s\n")
    messages = find_violations(tmp_path)
    assert len(messages) == 2
    assert all("higher layer" in m for m in messages)


def test_flags_cross_cutting_package_importing_a_layer(tmp_path: Path) -> None:
    _write(tmp_path, "security/a.py", "from app.tools import t\n")
    assert "cross-cutting" in find_violations(tmp_path)[0]


def test_flags_top_level_module_importing_zetaris(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "from app.zetaris import client\n")
    assert find_violations(tmp_path)


def test_zetaris_may_not_import_tools(tmp_path: Path) -> None:
    _write(tmp_path, "zetaris/a.py", "from app.tools import t\n")
    assert find_violations(tmp_path)
