"""Export app import topology as Graphviz DOT.

This script is analysis-only. It does not enforce architecture rules.
CI uses it to provide a topology artifact for PR review.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _module_name(app_root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(app_root).with_suffix("")
    return "app." + ".".join(rel.parts)


def _imports_from_file(file_path: Path) -> set[str]:
    text = file_path.read_text(encoding="utf-8-sig", errors="ignore")
    tree = ast.parse(text, filename=str(file_path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("app."):
                imports.add(node.module)
    return imports


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    app_root = repo_root / "app"
    out_path = repo_root / "doc" / "import_topology.dot"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    modules: dict[str, set[str]] = {}
    for file_path in sorted(app_root.rglob("*.py")):
        if "__pycache__" in file_path.parts:
            continue
        module = _module_name(app_root, file_path)
        imports = _imports_from_file(file_path)
        modules[module] = imports

    lines = ["digraph app_import_topology {", "  rankdir=LR;", '  node [shape=box, fontsize=10];']
    for module in sorted(modules.keys()):
        lines.append(f'  "{module}";')
    for module, imports in sorted(modules.items()):
        for dep in sorted(imports):
            if dep in modules:
                lines.append(f'  "{module}" -> "{dep}";')
    lines.append("}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
