"""Compatibility entrypoint for the canonical architecture guard.

The maintained rules live under ``tests/architecture`` so default pytest and
CI collect them. This file remains for operators that still run the historical
layered-suite command directly.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.architecture.test_dependency_rules import (  # noqa: E402
    test_current_backend_does_not_exceed_reviewed_architecture_debt as _test_dependency_ratchet,
)
from tests.architecture.test_layering_contracts import (  # noqa: E402
    test_auth_service_keeps_core_authentication_compatibility_import as _test_auth_contract,
    test_core_and_service_layers_do_not_import_legacy_ui as _test_ui_independence,
    test_engine_contract_remains_in_extracted_module_and_legacy_shim as _test_engine_contract,
)


def test_architecture_dependency_ratchet():
    _test_dependency_ratchet()


def test_core_layers_remain_independent_from_legacy_ui():
    _test_ui_independence()


def test_engine_extraction_compatibility_contract():
    _test_engine_contract()


def test_auth_service_compatibility_contract():
    _test_auth_contract()


if __name__ == "__main__":
    tests = (
        test_architecture_dependency_ratchet,
        test_core_layers_remain_independent_from_legacy_ui,
        test_engine_extraction_compatibility_contract,
        test_auth_service_compatibility_contract,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"\n{len(tests)} architecture-guard tests passed.")
