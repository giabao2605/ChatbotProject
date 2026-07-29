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


@dataclass(frozen=True, slots=True)
class _ImportRule:
    name: str
    blocked_prefixes: tuple[str, ...]
    allowed_prefixes: tuple[str, ...] = ()


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
    "mech_chatbot.db",
    "mech_chatbot.ingestion",
    "mech_chatbot.rag",
)
_CONFIG_UPWARD_PREFIXES = (
    "mech_chatbot.api",
    "mech_chatbot.application",
    "mech_chatbot.db",
    "mech_chatbot.evaluation",
    "mech_chatbot.ingestion",
    "mech_chatbot.llm",
    "mech_chatbot.rag",
    "mech_chatbot.workers",
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
    "PilotReplayExecutor",
    "QdrantClient",
    "Semaphore",
    "ThreadPoolExecutor",
    "_get_runtime_llm",
    "build_vision_model",
    "create_db_engine",
    "get_instance",
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


def _is_canonical_settings(path: Path) -> bool:
    return path.as_posix() == "config/settings.py"


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
    if relative.as_posix() != "services/__init__.py":
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


def _import_rules_for_package(package: str) -> tuple[_ImportRule, ...]:
    rules: list[_ImportRule] = []
    if package in _CORE_PACKAGES:
        rules.append(_ImportRule("core_ui_dependency", ("streamlit", "mech_chatbot.ui")))
    if package == "services":
        rules.append(
            _ImportRule("service_ui_dependency", ("streamlit", "mech_chatbot.ui"))
        )
    if package == "api":
        rules.append(_ImportRule("api_direct_data_access", _API_DATA_PREFIXES))
    if package == "application":
        rules.append(
            _ImportRule("application_external_dependency", _APPLICATION_EXTERNAL_PREFIXES)
        )
    if package == "config":
        rules.append(
            _ImportRule("config_upward_dependency", _CONFIG_UPWARD_PREFIXES)
        )
    if package == "db":
        rules.append(_ImportRule("db_upward_dependency", _DB_UPWARD_PREFIXES))
    if package == "ingestion":
        rules.append(
            _ImportRule("ingestion_rag_dependency", ("mech_chatbot.rag",))
        )
    if package == "rag":
        rules.append(_ImportRule("rag_evaluation_dependency", ("mech_chatbot.evaluation",)))
        rules.append(
            _ImportRule("rag_ingestion_dependency", ("mech_chatbot.ingestion",))
        )
    if package == "evaluation":
        rules.append(
            _ImportRule(
                "evaluation_private_rag_dependency",
                ("mech_chatbot.rag",),
                ("mech_chatbot.rag.execution",),
            )
        )
    return tuple(rules)


def _scan_import_rules(
    relative: Path,
    imports: Iterable[tuple[str, bool]],
) -> list[ArchitectureViolation]:
    package = relative.parts[0]
    rules = _import_rules_for_package(package)
    findings: list[ArchitectureViolation] = []
    for dependency, wildcard in imports:
        for rule in rules:
            blocked = _matches_prefix(dependency, rule.blocked_prefixes)
            allowed = _matches_prefix(dependency, rule.allowed_prefixes)
            if blocked and not allowed:
                findings.append(
                    ArchitectureViolation(rule.name, relative.as_posix(), dependency)
                )
        if wildcard:
            findings.append(
                ArchitectureViolation("wildcard_import", relative.as_posix(), dependency)
            )
    return findings


def _scan_direct_getenv(relative: Path, tree: ast.Module) -> list[ArchitectureViolation]:
    if _is_canonical_settings(relative):
        return []
    findings = []
    for node in ast.walk(tree):
        is_getenv = (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr == "getenv"
        )
        if is_getenv:
            findings.append(
                ArchitectureViolation("direct_getenv", relative.as_posix(), "os.getenv")
            )
    return findings


def _scan_dynamic_imports(
    relative: Path,
    tree: ast.Module,
) -> list[ArchitectureViolation]:
    if not relative.parts or relative.parts[0] != "db":
        return []
    findings = []
    for node in ast.walk(tree):
        function = node.func if isinstance(node, ast.Call) else None
        is_import_module = (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "importlib"
            and function.attr == "import_module"
        )
        if is_import_module:
            findings.append(
                ArchitectureViolation(
                    "db_dynamic_import",
                    relative.as_posix(),
                    "importlib.import_module",
                )
            )
    return findings


def _scan_api_calls(relative: Path, tree: ast.Module) -> list[ArchitectureViolation]:
    if relative.parts[0] != "api":
        return []
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) == "text":
            findings.append(
                ArchitectureViolation("api_raw_sql", relative.as_posix(), "sqlalchemy.text")
            )
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "engine"
            and function.attr in {"begin", "connect"}
        ):
            findings.append(
                ArchitectureViolation(
                    "api_engine_access", relative.as_posix(), f"engine.{function.attr}"
                )
            )
    return findings


def _scan_import_time_resources(
    relative: Path,
    tree: ast.Module,
) -> list[ArchitectureViolation]:
    findings = []
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
    return findings


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr == "environ"
    )


def _scan_import_time_environment_mutations(
    relative: Path,
    tree: ast.Module,
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for node in tree.body:
        mutated = False
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            mutated = any(
                isinstance(target, ast.Subscript) and _is_os_environ(target.value)
                for target in targets
            )
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            function = node.value.func
            mutated = (
                isinstance(function, ast.Attribute)
                and _is_os_environ(function.value)
                and function.attr in {"clear", "pop", "setdefault", "update"}
            )
        if mutated:
            findings.append(
                ArchitectureViolation(
                    "import_time_environment_mutation",
                    relative.as_posix(),
                    "os.environ",
                )
            )
    return findings


def _scan_python_file(source_root: Path, path: Path) -> list[ArchitectureViolation]:
    relative = path.relative_to(source_root)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    imports = _imports(_module_name(source_root, path), tree)
    return [
        *_scan_import_rules(relative, imports),
        *_scan_direct_getenv(relative, tree),
        *_scan_dynamic_imports(relative, tree),
        *_scan_api_calls(relative, tree),
        *_scan_import_time_resources(relative, tree),
        *_scan_import_time_environment_mutations(relative, tree),
        *_scan_service_exports(relative, tree),
    ]


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

    return sorted(
        finding
        for path in _iter_python_files(source_root)
        for finding in _scan_python_file(source_root, path)
    )


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
        "application_external_dependency": "Phase 5",
        "config_upward_dependency": "Phase 5",
        "db_dynamic_import": "Phase 5",
        "db_upward_dependency": "Phase 5",
        "direct_getenv": "Phase 5",
        "evaluation_private_rag_dependency": "Phase 4",
        "import_time_environment_mutation": "Phase 5",
        "import_time_resource": "Phase 5",
        "ingestion_rag_dependency": "Phase 5",
        "rag_ingestion_dependency": "Phase 5",
        "service_flat_export": "Phase 6",
        "service_root_module": "Phase 6",
        "wildcard_import": "Phase 6",
    }
    reason_by_rule = {
        "api_direct_data_access": "Existing FastAPI data-access debt",
        "api_engine_access": "Existing FastAPI engine access debt",
        "api_raw_sql": "Existing FastAPI raw SQL debt",
        "application_external_dependency": "Existing application dependency on an upper or adapter layer",
        "config_upward_dependency": "Existing config callback into a runtime layer",
        "db_dynamic_import": "Existing dynamic DB callback into an upper layer",
        "db_upward_dependency": "Existing reverse dependency from DB into an upper layer",
        "direct_getenv": "Existing configuration read outside canonical settings",
        "evaluation_private_rag_dependency": "Existing evaluation dependency on private RAG implementation",
        "import_time_environment_mutation": "Existing process environment mutation at module import time",
        "import_time_resource": "Existing resource constructed at module import time",
        "ingestion_rag_dependency": "Existing ingestion dependency on RAG implementation",
        "rag_ingestion_dependency": "Existing RAG dependency on ingestion implementation",
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
