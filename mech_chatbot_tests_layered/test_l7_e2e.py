"""L7 - UI / E2E theo vai tro (NGHIEM THU THU CONG).

L7 KHONG phai noi san bug logic. Chi chay SAU khi L1-L6 da xanh.
Day chu yeu la checklist thu cong (P0 -> P2) tren staging clone.
File nay chi cung cap smoke-check moi truong + nhac viec.
"""
import pytest

pytestmark = [pytest.mark.l7]


@pytest.mark.slow
def test_env_smoke_server_reachable(rag_server_url):
    """Kiem nhanh: server E2E dang song truoc khi vao checklist thu cong."""
    requests = pytest.importorskip("requests")
    r = requests.get(rag_server_url + "/docs", timeout=10)
    assert r.status_code < 500


@pytest.mark.skip(reason="L7 la NGHIEM THU THU CONG: chay Checklist P0->P2 tren staging. Xem README.")
def test_manual_checklist_signoff_TEMPLATE():
    # Thuc hien toan bo Checklist Test P0 -> P2 tren moi truong staging clone.
    # Ghi ket qua tung muc (Dat / Loi + log). Loi phai quay ve sua o tang tuong ung (L1-L6).
    raise NotImplementedError
