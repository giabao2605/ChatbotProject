# -*- coding: utf-8 -*-
# FIX crash native 0xC0000005 (Windows): `onnxruntime` (do fastembed/BM25 keo vao)
# bi ACCESS VIOLATION khi load .pyd trong ngu canh da co san stack nang cua
# `torch` + `sentence_transformers` (+ cac lib ingest khac). Da xac minh: chi can
# nap `onnxruntime` TRUOC MOI import nang khac la het crash.
#
# Dat o dau package `mech_chatbot` => chay TRUOC bat cu submodule nao
# (worker ingest, rag worker, api server, app Streamlit...) nen bao ve toan bo
# cac duong vao. Import that bai (vd moi truong khong co onnxruntime) => bo qua.
try:
    import onnxruntime as _onnxruntime  # noqa: F401
except Exception:
    pass