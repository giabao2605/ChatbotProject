from __future__ import annotations

import importlib

import pytest

import mech_chatbot


pytestmark = pytest.mark.unit


def test_optional_native_preload_ignores_only_the_requested_missing_module(
    monkeypatch: pytest.MonkeyPatch,
):
    def missing_requested(name: str):
        raise ModuleNotFoundError(name=name)

    monkeypatch.setattr(importlib, "import_module", missing_requested)

    assert mech_chatbot._preload_optional_native("optional_native") is False


def test_optional_native_preload_propagates_broken_transitive_import(
    monkeypatch: pytest.MonkeyPatch,
):
    def missing_transitive(name: str):
        raise ModuleNotFoundError(name="native_runtime_dependency")

    monkeypatch.setattr(importlib, "import_module", missing_transitive)

    with pytest.raises(ModuleNotFoundError) as exc_info:
        mech_chatbot._preload_optional_native("optional_native")
    assert exc_info.value.name == "native_runtime_dependency"


def test_optional_native_preload_propagates_native_loader_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    def broken_loader(name: str):
        raise OSError("native loader failed")

    monkeypatch.setattr(importlib, "import_module", broken_loader)

    with pytest.raises(OSError, match="native loader failed"):
        mech_chatbot._preload_optional_native("optional_native")
