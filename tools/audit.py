"""Architecture audit: measure what makes maniac hard to change, and ratchet it.

Each metric counts one kind of structural debt the 2026-10-10 review found
(docs/STATE.md): a question answered in several places, imports against the
intended layering, functions too complex to read, code nothing runs.
`audit.toml` holds, per metric, a ceiling and a target. The audit fails when
a metric rises above its ceiling, so debt can only shrink; `--update` lowers
each ceiling to what is measured once work has brought it down. Ceilings
never rise through `--update`: raising one is a decision, made by editing
`audit.toml` in a commit that says why.

Usage: `python tools/audit.py [--update]` (`just audit`).
"""

import ast
import fnmatch
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "maniac"
CONFIG = ROOT / "audit.toml"


@dataclass(frozen=True)
class Measure:
    value: int
    where: list[str]
    """What makes up the value, so a failure says what to look at."""


# -- source helpers -----------------------------------------------------------


def _modules() -> dict[str, ast.Module]:
    """Every module of the package, keyed by its path relative to the root."""
    return {
        str(path.relative_to(ROOT)): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(PACKAGE.rglob("*.py"))
    }


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _called_names(tree: ast.Module) -> Counter[str]:
    """Names called in a module, plain (`f()`) or through a module (`m.f()`)."""
    called: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called[func.id] += 1
            elif isinstance(func, ast.Attribute):
                called[func.attr] += 1
    return called


def _handled_exceptions(tree: ast.Module) -> Counter[str]:
    """Exception names caught by `except` clauses, tuples included."""
    caught: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            types = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            for t in types:
                name = t.id if isinstance(t, ast.Name) else getattr(t, "attr", None)
                if name:
                    caught[name] += 1
    return caught


# -- metrics ------------------------------------------------------------------


def modules_calling(cfg: dict) -> Measure:
    """Modules outside `allowed` that call any of `names`."""
    names = set(cfg["names"])
    where = [
        path
        for path, tree in _modules().items()
        if not _matches(path, cfg.get("allowed", []))
        and names & set(_called_names(tree))
    ]
    return Measure(len(where), where)


def handlers_of(cfg: dict) -> Measure:
    """`except` clauses outside `allowed` catching any of `names`."""
    names = set(cfg["names"])
    where = []
    for path, tree in _modules().items():
        if _matches(path, cfg.get("allowed", [])):
            continue
        caught = _handled_exceptions(tree)
        count = sum(caught[name] for name in names)
        if count:
            where.append(f"{path} ({count})")
    return Measure(sum(int(w.rsplit("(", 1)[1][:-1]) for w in where), where)


def _import_graph():
    import grimp
    import networkx as nx

    sys.path.insert(0, str(ROOT))
    graph = grimp.build_graph("maniac")
    directed = nx.DiGraph()
    for module in graph.modules:
        for imported in graph.find_modules_directly_imported_by(module):
            if imported.startswith("maniac"):
                directed.add_edge(module, imported)
    return graph, directed


def _package(module: str, depth: int) -> str:
    """The package a module belongs to, at most `depth` levels below `maniac`.

    A module directly under `maniac` (`maniac.config`) is a package of its
    own; any other belongs to its enclosing package, so `sources/resolution.py`
    and `sources/providers/mise.py` fall in `maniac.sources` and
    `maniac.sources.providers`.
    """
    parts = module.split(".")
    is_package = (ROOT.joinpath(*parts) / "__init__.py").exists()
    enclosing = parts if is_package else parts[:-1]
    if enclosing == ["maniac"]:
        enclosing = parts
    return ".".join(enclosing[: depth + 1])


def package_cycles(cfg: dict) -> Measure:
    """Package-level import cycles: groups of packages importing each other."""
    import networkx as nx

    _, modules = _import_graph()
    ignore = cfg.get("ignore", [])
    packages = nx.DiGraph()
    for a, b in modules.edges:
        pa, pb = _package(a, cfg["depth"]), _package(b, cfg["depth"])
        if pa != pb and not _matches(pa, ignore) and not _matches(pb, ignore):
            packages.add_edge(pa, pb)
    cycles = [
        " <-> ".join(sorted(c))
        for c in nx.strongly_connected_components(packages)
        if len(c) > 1
    ]
    return Measure(len(cycles), sorted(cycles))


def module_cycles(cfg: dict) -> Measure:
    """Module-level import cycles, outside the packages `ignore` names."""
    import networkx as nx

    _, modules = _import_graph()
    keep = [m for m in modules if not _matches(m, cfg.get("ignore", []))]
    cycles = [
        ", ".join(sorted(c))
        for c in nx.strongly_connected_components(modules.subgraph(keep))
        if len(c) > 1
    ]
    return Measure(len(cycles), sorted(cycles))


def forbidden_imports(cfg: dict) -> Measure:
    """Module imports from one package into another the layering forbids."""
    _, modules = _import_graph()
    where = [
        f"{a} -> {b}"
        for importer, imported in cfg["rules"]
        for a, b in modules.edges
        if (a == importer or a.startswith(importer + "."))
        and (b == imported or b.startswith(imported + "."))
    ]
    return Measure(len(where), sorted(where))


def over_complex(cfg: dict) -> Measure:
    """Functions whose cognitive complexity (complexipy) exceeds `limit`."""
    import complexipy

    where = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for function in complexipy.file_complexity(str(path)).functions:
            if function.complexity > cfg["limit"]:
                where.append(
                    f"{path.relative_to(ROOT)}:{function.line_start} "
                    f"{function.name} ({function.complexity})"
                )
    return Measure(len(where), where)


def dead_code(cfg: dict) -> Measure:
    """What vulture finds unused in the package (tests do not count as use)."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "vulture",
            str(PACKAGE),
            "--min-confidence",
            str(cfg["min_confidence"]),
            "--ignore-decorators",
            ",".join(cfg.get("ignore_decorators", [])),
            "--ignore-names",
            ",".join(cfg.get("ignore_names", [])),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    where = [
        line.replace(str(ROOT) + "/", "") for line in result.stdout.splitlines() if line
    ]
    return Measure(len(where), where)


METRICS: dict[str, Callable[[dict], Measure]] = {
    "modules_calling": modules_calling,
    "handlers_of": handlers_of,
    "package_cycles": package_cycles,
    "module_cycles": module_cycles,
    "forbidden_imports": forbidden_imports,
    "over_complex": over_complex,
    "dead_code": dead_code,
}


# -- ratchet ------------------------------------------------------------------


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        if all(isinstance(v, str | int) for v in value) and len(value) <= 3:
            return "[" + ", ".join(_toml_value(v) for v in value) + "]"
        return "[\n" + "".join(f"  {_toml_value(v)},\n" for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items()) + " }"
        )
    raise TypeError(value)


def _write(config: dict) -> None:
    lines = [config["_header"].rstrip(), ""]
    for name, metric in config["metric"].items():
        lines.append(f"[metric.{name}]")
        lines += [f"{key} = {_toml_value(value)}" for key, value in metric.items()]
        lines.append("")
    CONFIG.write_text("\n".join(lines), encoding="utf-8")


def _header() -> str:
    text = CONFIG.read_text(encoding="utf-8")
    return text[: text.index("[metric.")]


def main(argv: list[str]) -> int:
    update = "--update" in argv
    verbose = "-v" in argv or "--verbose" in argv
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    failed, lowered = [], []
    width = max(len(name) for name in config["metric"])
    print(f"{'metric':<{width}}  {'now':>4}  {'ceiling':>7}  {'target':>6}")
    for name, metric in config["metric"].items():
        measure = METRICS[metric["kind"]](metric)
        ceiling, target = metric["ceiling"], metric["target"]
        status = ""
        if measure.value > ceiling:
            status = "  WORSE"
            failed.append(name)
        elif measure.value < ceiling:
            status = "  better: `just audit --update` lowers the ceiling"
            lowered.append(name)
            if update:
                metric["ceiling"] = measure.value
        elif measure.value <= target:
            status = "  at target"
        print(f"{name:<{width}}  {measure.value:>4}  {ceiling:>7}  {target:>6}{status}")
        if measure.where and (verbose or name in failed):
            for item in measure.where:
                print(f"{'':<{width}}    {item}")
            if name in failed:
                print(f"{'':<{width}}    ({metric['why']})")
    if update and lowered:
        config["_header"] = _header()
        _write(config)
        print(f"\nlowered: {', '.join(lowered)}")
    if failed:
        print(
            f"\n{len(failed)} metric(s) worse than their ceiling: {', '.join(failed)}."
            "\nEach line above says what grew; `just audit -v` lists every metric's"
            " contents."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
