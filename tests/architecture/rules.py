"""AST-based architecture ratchet for the backend source tree.

The ratchet permits known debt from a reviewed allowlist while rejecting any
new occurrence. Repeated findings are intentionally retained so reducing debt
is accepted and adding another occurrence fails.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True, order=True, slots=True)
class ArchitectureViolation:
    rule: str
    path: str
    dependency: str


_API_DATA_PREFIXES = (
    "sqlalchemy",
    "mech_chatbot.db.engine",
    "mech_chatbot.db.repository",
    "mech_chatbot.db.repositories",
)
_APPLICATION_EXTERNAL_PREFIXES = (
    "fastapi",
    "httpx",
    "langchain_openai",
    "openai",
    "qdrant_client",
    "requests",
    "sqlalchemy",
    "streamlit",
    "mech_chatbot.adapters",
)
_DB_UPWARD_PREFIXES = (
    "mech_chatbot.api",
    "mech_chatbot.evaluation",
    "mech_chatbot.ingestion",
    "mech_chatbot.rag",
)
_RESOURCE_FACTORIES = {
    "ChatOpenAI",
    "OpenAI",
    "QdrantClient",
    "ThreadPoolExecutor",
    "build_vision_model",
    "create_db_engine",
}
_CORE_PACKAGES = {"api", "application", "config", "db", "ingestion", "llm", "rag", "workers"}


def _iter_python_files(source_root: Path) -> Iterable[Path]:
    for path in sorted(source_root.rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path


def _module_name(source_root: Path, path: Path) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    parts = (source_root.name, *relative.parts)
    return ".".join(parts)


def _resolve_import(module_name: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package_parts = module_name.split(".")[:-1]
    keep = max(0, len(package_parts) - (node.level - 1))
    suffix = (node.module or "").split(".") if node.module else []
    return ".".join((*package_parts[:keep], *suffix))


def _imports(module_name: str, tree: ast.AST) -> Iterable[tuple[str, bool]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, False
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_import(module_name, node)
            for alias in node.names:
                yield target, alias.name == "*"


def _matches_prefix(module: str, prefixes: Sequence[str]) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)


def _call_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _is_config_or_bootstrap(path: Path) -> bool:
    return (
        "config" in path.parts
        or path.name == "bootstrap.py"
        or path.stem == "config"
        or path.stem.endswith("_config")
    )


def _literal_string_list(node: ast.AST) -> tuple[str, ...]:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return ()
    values: list[str] = []
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            values.append(element.value)
    return tuple(values)


def _assigned_names(node: ast.AST) -> tuple[str, ...]:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return ()
    return tuple(element.id for element in node.elts if isinstance(element, ast.Name))


def _scan_service_exports(relative: Path, tree: ast.Module) -> list[ArchitectureViolation]:
    if not relative.parts or relative.parts[0] != "services":
        return []
    findings: list[ArchitectureViolation] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if target.id == "__all__":
            findings.extend(
                ArchitectureViolation("service_flat_export", relative.as_posix(), symbol)
                for symbol in _literal_string_list(node.value)
            )
        elif target.id == "_SERVICE_MODULES":
            findings.extend(
                ArchitectureViolation("service_root_module", relative.as_posix(), name)
                for name in _assigned_names(node.value)
            )
    return findings


def scan_repository(
    source_root: Path,
    *,
    required_packages: Sequence[str] = (),
) -> list[ArchitectureViolation]:
    """Return every current architecture finding without applying an allowlist."""

    if not source_root.is_dir():
        raise FileNotFoundError(f"Architecture source root does not exist: {source_root}")
    missing = [name for name in required_packages if not (source_root / name).is_dir()]
    if missing:
        raise FileNotFoundError(
            "Required architecture packages do not exist: " + ", ".join(sorted(missing))
        )

    findings: list[ArchitectureViolation] = []
    for path in _iter_python_files(source_root):
        relative = path.relative_to(source_root)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        module_name = _module_name(source_root, path)
        imports = tuple(_imports(module_name, tree))

        for dependency, wildcard in imports:
            if relative.parts[0] in _CORE_PACKAGES and _matches_prefix(
                dependency, ("streamlit", "mech_chatbot.ui")
            ):
                findings.append(
                    ArchitectureViolation("core_ui_dependency", relative.as_posix(), dependency)
                )
            if relative.parts[0] == "services" and _matches_prefix(
                dependency, ("streamlit", "mech_chatbot.ui")
            ):
                findings.append(
                    ArchitectureViolation("service_ui_dependency", relative.as_posix(), dependency)
                )
            if relative.parts[0] == "api" and _matches_prefix(dependency, _API_DATA_PREFIXES):
                findings.append(
                    ArchitectureViolation("api_direct_data_access", relative.as_posix(), dependency)
                )
            if relative.parts[0] == "application" and _matches_prefix(
                dependency, _APPLICATION_EXTERNAL_PREFIXES
            ):
                findings.append(
                    ArchitectureViolation(
                        "application_external_dependency", relative.as_posix(), dependency
                    )
                )
            if relative.parts[0] == "db" and _matches_prefix(dependency, _DB_UPWARD_PREFIXES):
                findings.append(
                    ArchitectureViolation("db_upward_dependency", relative.as_posix(), dependency)
                )
            if relative.parts[0] == "rag" and _matches_prefix(
                dependency, ("mech_chatbot.evaluation",)
            ):
                findings.append(
                        ArchitectureViolation("rag_evaluation_dependency", relative.as_posix(), dependency)
                )
            if (
                relative.parts[0] == "evaluation"
                and _matches_prefix(dependency, ("mech_chatbot.rag",))
                and not _matches_prefix(dependency, ("mech_chatbot.rag.execution",))
            ):
                findings.append(
                    ArchitectureViolation(
                        "evaluation_private_rag_dependency", relative.as_posix(), dependency
                    )
                )
            if wildcard:
                findings.append(
                    ArchitectureViolation("wildcard_import", relative.as_posix(), dependency)
                )

        if not _is_config_or_bootstrap(relative):
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr == "getenv"
                ):
                    findings.append(
                        ArchitectureViolation("direct_getenv", relative.as_posix(), "os.getenv")
                    )

        if relative.parts[0] == "api":
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _call_name(node) == "text":
                    findings.append(
                        ArchitectureViolation("api_raw_sql", relative.as_posix(), "sqlalchemy.text")
                    )
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "engine"
                    and node.func.attr in {"begin", "connect"}
                ):
                    findings.append(
                        ArchitectureViolation(
                            "api_engine_access", relative.as_posix(), f"engine.{node.func.attr}"
                        )
                    )

        for node in tree.body:
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                value = node.value
            if isinstance(value, ast.Call) and _call_name(value) in _RESOURCE_FACTORIES:
                findings.append(
                    ArchitectureViolation(
                        "import_time_resource", relative.as_posix(), _call_name(value)
                    )
                )

        findings.extend(_scan_service_exports(relative, tree))

    return sorted(findings)


def load_allowlist(path: Path) -> list[ArchitectureViolation]:
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("Architecture allowlist must be a version 1 JSON object")
    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Architecture allowlist entries must be a JSON list")
    allowed: list[ArchitectureViolation] = []
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("Architecture allowlist entry must be an object")
        count = item.get("count")
        if not isinstance(count, int) or count < 1:
            raise ValueError("Architecture allowlist count must be a positive integer")
        violation = ArchitectureViolation(
            rule=str(item.get("rule") or ""),
            path=str(item.get("path") or ""),
            dependency=str(item.get("dependency") or ""),
        )
        if not all((violation.rule, violation.path, violation.dependency)):
            raise ValueError("Architecture allowlist entry has an empty identity field")
        if not item.get("removal_phase") or not item.get("reason"):
            raise ValueError("Architecture allowlist entry needs removal_phase and reason")
        allowed.extend([violation] * count)
    return allowed


def unexpected_violations(
    current: Iterable[ArchitectureViolation],
    allowed: Iterable[ArchitectureViolation],
) -> list[ArchitectureViolation]:
    return sorted((Counter(current) - Counter(allowed)).elements())


def stale_allowances(
    current: Iterable[ArchitectureViolation],
    allowed: Iterable[ArchitectureViolation],
) -> list[ArchitectureViolation]:
    return sorted((Counter(allowed) - Counter(current)).elements())


def format_violations(violations: Iterable[ArchitectureViolation]) -> str:
    counts = Counter(violations)
    return "\n".join(
        f"{item.rule}: {item.path} -> {item.dependency} (count={count})"
        for item, count in sorted(counts.items())
    )


def write_allowlist(path: Path, violations: Iterable[ArchitectureViolation]) -> None:
    phase_by_rule = {
        "api_direct_data_access": "Phase 2",
        "api_engine_access": "Phase 2",
        "api_raw_sql": "Phase 2",
        "db_upward_dependency": "Phase 5",
        "direct_getenv": "Phase 5",
        "evaluation_private_rag_dependency": "Phase 4",
        "import_time_resource": "Phase 5",
        "service_flat_export": "Phase 6",
        "service_root_module": "Phase 6",
        "wildcard_import": "Phase 6",
    }
    reason_by_rule = {
        "api_direct_data_access": "Existing FastAPI data-access debt",
        "api_engine_access": "Existing FastAPI engine access debt",
        "api_raw_sql": "Existing FastAPI raw SQL debt",
        "db_upward_dependency": "Existing reverse dependency from DB into an upper layer",
        "direct_getenv": "Existing configuration read outside a config/bootstrap module",
        "evaluation_private_rag_dependency": "Existing evaluation dependency on private RAG implementation",
        "import_time_resource": "Existing resource constructed at module import time",
        "service_flat_export": "Existing flat services compatibility export",
        "service_root_module": "Existing module in the flat services compatibility facade",
        "wildcard_import": "Existing wildcard compatibility import",
    }
    entries = []
    for item, count in sorted(Counter(violations).items()):
        entry = asdict(item)
        entry.update(
            count=count,
            removal_phase=phase_by_rule.get(item.rule, "Phase 5"),
            reason=reason_by_rule.get(item.rule, "Existing reviewed architecture debt"),
        )
        entries.append(entry)
    payload = {
        "version": 1,
        "policy": "Current count must equal the reviewed count; update debt and allowlist together.",
        "entries": entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or snapshot backend architecture debt")
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--write-allowlist", type=Path)
    args = parser.parse_args()
    findings = scan_repository(args.source_root)
    if args.write_allowlist:
        write_allowlist(args.write_allowlist, findings)
    else:
        print(format_violations(findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
