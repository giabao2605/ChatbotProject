from datetime import timedelta

import pytest

from mech_chatbot.governance import query_activation_contract as contract


def test_sequential_contract_has_explicit_bounded_authorization():
    version = 'query-decomposition-sequential-100-v1'
    authorization = contract.query_pilot_authorization(version)
    assert authorization['pilot_contract_version'] == version
    assert authorization['minimum_elapsed_hours'] == 0
    assert authorization['dispatch_mode'] == 'sequential_after_completion'
    assert authorization['eligible_request_count'] == 100
    assert contract.query_pilot_duration_bounds(version) == (
        timedelta(minutes=10), timedelta(hours=6),
    )


def test_legacy_contract_remains_unchanged():
    assert contract.query_pilot_authorization(contract.QUERY_PILOT_CONTRACT_VERSION) == contract.QUERY_PILOT_AUTHORIZATION
    assert contract.query_pilot_duration_bounds(contract.QUERY_PILOT_CONTRACT_VERSION)[0] == timedelta(hours=24, minutes=10)


def test_unknown_contract_is_rejected():
    with pytest.raises(ValueError, match='pilot_contract_version_invalid'):
        contract.query_pilot_authorization('unknown')


@pytest.mark.parametrize("module_name", ["query_decomposition_pilot", "query_decomposition_pilot_launch"])
def test_prepare_cli_forwards_explicit_contract(monkeypatch, tmp_path, module_name):
    import importlib
    module = importlib.import_module(f"scripts.ops.{module_name}")
    captured = []
    consolidated = module_name.endswith("_launch")
    function = "prepare_consolidated_launch" if consolidated else "prepare_pilot_launch_packet"
    monkeypatch.setattr(module, function, lambda **kwargs: captured.append(kwargs) or {})
    arguments = ["prepare", "--source-root", str(tmp_path), "--source-commit", "a" * 40,
                 "--manifest", str(tmp_path / "manifest"), "--output-dir", str(tmp_path / "out"),
                 "--owner", "owner", "--pilot-contract-version", contract.QUERY_SEQUENTIAL_PILOT_CONTRACT_VERSION]
    arguments += (["--activation-draft", str(tmp_path / "draft")] if consolidated else
                  ["--activation-bundle", str(tmp_path / "bundle"), "--activation-finalization", str(tmp_path / "final")])
    assert module.main(arguments) == 0
    assert captured[0]["pilot_contract_version"] == contract.QUERY_SEQUENTIAL_PILOT_CONTRACT_VERSION
