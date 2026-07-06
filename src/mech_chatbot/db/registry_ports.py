# -*- coding: utf-8 -*-
"""P1.4 — Seam đảo chiều phụ thuộc db → ingestion.

Trước P1.4, các repository trong `db/repositories/*` import trực tiếp các
registry ở `mech_chatbot.ingestion.*` (cross-layer L2 → L4). File này làm
điểm trung gian (port) để tang db KHÔNG còn tham chiếu trực tiếp ingestion.

- Mặc định: **lazy-import** từ ingestion khi được gọi → GIỮ NGUYÊN hành vi cũ
  (không có import ingestion ở top-level nên không tạo vòng lặp import tĩnh).
- Tang trên (ingestion) có thể gọi `register(name, fn)` để đẩy (push) triển khai
  thực sự → đảo chiều hoàn toàn (dành cho P2). Khi đã register, port dùng luôn
  bản được đăng ký, không lazy-import nữa.
"""
import importlib

_IMPLS = {}

# ten port -> module ingestion cung cap ham cung ten (fallback lazy-import)
_SOURCES = {
	"resolve_domain_by_department": "mech_chatbot.ingestion.domain_registry",
	"resolve_security_by_department": "mech_chatbot.ingestion.domain_registry",
	"canonical_label": "mech_chatbot.ingestion.doc_type_registry",
	"normalize_material": "mech_chatbot.ingestion.material_registry",
	"refresh_cache": "mech_chatbot.ingestion.material_registry",
	"resolve_site_by_department": "mech_chatbot.ingestion.site_registry",
}


def register(name, fn):
	"""Tang tren dang ky trien khai that su cho mot port (inversion of control)."""
	_IMPLS[name] = fn


def _resolve(name):
	fn = _IMPLS.get(name)
	if fn is not None:
		return fn
	mod = importlib.import_module(_SOURCES[name])
	return getattr(mod, name)


def resolve_domain_by_department(*args, **kwargs):
	return _resolve("resolve_domain_by_department")(*args, **kwargs)


def resolve_security_by_department(*args, **kwargs):
	return _resolve("resolve_security_by_department")(*args, **kwargs)


def canonical_label(*args, **kwargs):
	return _resolve("canonical_label")(*args, **kwargs)


def normalize_material(*args, **kwargs):
	return _resolve("normalize_material")(*args, **kwargs)


def refresh_cache(*args, **kwargs):
	return _resolve("refresh_cache")(*args, **kwargs)


def resolve_site_by_department(*args, **kwargs):
	return _resolve("resolve_site_by_department")(*args, **kwargs)
