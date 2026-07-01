"""L6 - API & Worker (dong thoi / hang doi).

Can RAG server dang chay (RAG_SERVER_URL) va/hoac DB (RUN_DB_TESTS=1).
Khoi dong truoc khi chay:
    python run_server.py     # RAG server, cong 8100
    python run_worker.py     # worker xu ly hang doi
"""
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.l6]


class TestApiHealth:
    def test_server_responds(self, rag_server_url):
        requests = pytest.importorskip("requests")
        last = None
        for path in ("/health", "/healthz", "/", "/docs"):
            try:
                r = requests.get(rag_server_url + path, timeout=10)
                last = r.status_code
                if r.status_code < 500:
                    return
            except Exception as e:  # pragma: no cover
                last = repr(e)
                continue
        pytest.fail("RAG server khong phan hoi tren cac endpoint thu nghiem (last=%s)" % last)


@pytest.mark.skip(reason="MAU L6-2/L6-3: test hang doi & race condition. Xem README.")
@pytest.mark.l6
class TestQueueTemplate:
    def test_concurrent_jobs_no_stuck_TEMPLATE(self, db_engine):
        # GOI Y: day 10-20 job dong thoi -> assert khong job nao ket trang thai 'running';
        #   kiem priority chay truoc, retry tay, cancel (CanceledBy/CanceledAt), waiting_quota, ETA.
        raise NotImplementedError

    def test_two_workers_no_double_pick_TEMPLATE(self, db_engine):
        # GOI Y: mo phong 2 worker cung goi get_pending_job -> 1 job KHONG bi xu ly trung.
        raise NotImplementedError
