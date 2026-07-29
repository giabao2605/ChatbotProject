# -*- coding: utf-8 -*-
# FIX crash native 0xC0000005 (Windows): `pyarrow` va `onnxruntime` can duoc nap
# theo thu tu on dinh truoc stack `torch` + `sentence_transformers`. Neu pyarrow
# chi duoc nap muon qua pandas/sklearn trong luc onnxruntime da khoi tao, CPython
# co the in "Windows fatal exception: access violation" nhung van tra exit 0.
#
# Dat o dau package `mech_chatbot` => chay TRUOC bat cu submodule nao
# (worker ingest, rag worker, api server, app Streamlit...) nen bao ve toan bo
# cac duong vao. Import that bai vi dependency tuy chon khong co => bo qua.
import importlib


def _preload_optional_native(module_name: str) -> bool:
    """Preload an optional native module and fail fast when its install is broken."""

    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return False
        raise
    return True


_preload_optional_native("pyarrow")
_preload_optional_native("onnxruntime")
