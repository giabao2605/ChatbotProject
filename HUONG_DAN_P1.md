# HƯỚNG DẪN NÂNG CẤP P1 — Đa khu / đa phòng ban, hàng đợi & vận hành

> Bản này nối tiếp P0. Mọi thay đổi đều **tương thích ngược**: nếu chưa chạy migration P1, hệ thống vẫn chạy như P0 (các tính năng mới sẽ ẩn/không có dữ liệu).

---

## 0. TÓM TẮT P1 ĐÃ LÀM

| # | Hạng mục | Trạng thái | Tệp chính |
|---|----------|-----------|-----------|
| P1.1 | Quản lý phòng ban / thư mục trong UI | ✅ | `ui/pages/users.py` (tab "Phòng ban & Khu") |
| P1.2 | Lớp **Khu (Site)** trên phòng ban + RBAC = Khu × Phòng × Mức mật | ✅ | `ingestion/site_registry.py`, `auth/service.py`, `rag/service.py` |
| P1.3 | Trích dẫn nguồn trong chat (DocID, version, khu) | ✅ | `rag/service.py` (`build_source_citations`) |
| P1.4 | Lọc theo phòng ban & khu ở Kho tài liệu + Hàng đợi | ✅ | `ui/pages/documents.py`, `ui/pages/queue.py` |
| P1.5 | Hàng đợi: ETA, ưu tiên, retry tay, huỷ job | ✅ | `ui/pages/queue.py`, `db/repository.py` |
| P1.6 | Dashboard theo từng phòng ban | ✅ | `ui/pages/dashboard.py`, `db/repository.py` |
| P1.7 | Đối soát SQL ↔ Qdrant (vector mồ côi, doc kẹt "deleting") | ✅ | `scripts/danger_ops/reconcile_sql_qdrant.py` |
| P1.8 | Backup tự động (SQL full+log + snapshot Qdrant) | ✅ | `scripts/ops/backup_system.py` |

---

## 1. CHẠY MIGRATION P1 (BẮT BUỘC, LÀM TRƯỚC)

Migration **idempotent** (chạy lại nhiều lần không sao). Luôn dùng cờ `-I`:

```powershell
sqlcmd -S localhost\SQLEXPRESS -d Mech_Chatbot_DB -I -i database\migrations\p1_multi_site_queue_admin.sql
```

Migration này sẽ:
- Tạo bảng `Departments` (danh mục phòng ban + lĩnh vực + khu mặc định), `Sites` (danh mục khu), `UserSites` (gán khu cho user).
- Thêm cột vào `IngestionJobs`: `Priority` (mặc định 100), `MaxPages`, `CanceledBy`, `CanceledAt`.
- Tạo index cho hàng đợi (`Status, Priority`) và cho lọc tài liệu (`Site, Domain`).
- **Tự seed** `Departments` từ dữ liệu hiện có (UserDepartments + TaiLieu) và map khu mặc định theo bảng dưới.

> Cột `Site` trên bảng `TaiLieu` đã có từ P0 (init script). Migration P1 chỉ backfill và đánh index.

### Bản đồ Khu ↔ Phòng ban (mặc định)
| Phòng ban | Khu (Site) |
|-----------|------------|
| To_Han, To_Dap, To_Son, To_Nham, To_Phoi, To_Tien_Phay, To_Dong_Goi, To_Ban_Le, Bang_Ke, Gia_Cong_Ngoai | `XUONG_CO_KHI` |
| Ky_Thuat | `PHONG_KY_THUAT` |
| Ke_Toan | `VP_KE_TOAN` |
| Nhan_Su | `VP_NHAN_SU` |
| (còn lại) | `HQ` |

Muốn đổi map: vào UI **Người dùng → Phòng ban & Khu**, đặt "Khu mặc định" cho từng phòng (ghi đè map cứng trong code).

---

## 2. CƠ CHẾ RBAC MỚI (Khu × Phòng × Mức mật)

Một câu hỏi của user chỉ truy xuất được tài liệu thoả **đồng thời 3 điều kiện**:
1. **Phòng ban**: `metadata.phong_ban_quyen` nằm trong `allowed_departments` của user.
2. **Mức mật**: `metadata.security_level` ≤ mức mật tối đa (`UserSecurityClearance.MaxLevel`).
3. **Khu (mới)**: `metadata.site` nằm trong `allowed_sites` của user — **HOẶC** user không bị giới hạn khu (bảng `UserSites` rỗng → thấy mọi khu).

> Để **không khoá nhầm** tài liệu cũ (chưa có `site` trong vector), bộ lọc khu cho phép cả điều kiện "site rỗng". Sau khi re-embed (mục 5) thì siết chặt theo khu mới đầy đủ.

Gán quyền cho user: UI **Người dùng → Danh sách người dùng → (mở user) → Phân quyền RBAC**: chọn phòng ban, khu, mức mật, trạng thái active.

---

## 3. CÁC TÍNH NĂNG MỚI TRÊN UI

### Kho tài liệu (`documents.py`)
- Thêm 3 bộ lọc: **Trạng thái / Phòng ban / Khu**.
- Mỗi tài liệu hiển thị thêm: **Khu, Lĩnh vực (domain), Mức mật**.

### Hàng đợi (`queue.py`)
- 3 thẻ tổng quan ở đầu: **Đang chờ / TB mỗi job / Dự kiến xử xong (ETA)**.
- Lọc theo **phòng ban**; sắp xếp theo **ưu tiên** (giống worker).
- Mỗi job (admin): **đặt ưu tiên**, **thử lại**, **huỷ job**. (Số ưu tiên nhỏ hơn = xử lý trước; 100 = thường, <50 = gấp.)

### Dashboard (`dashboard.py`)
- Thêm bảng **Thống kê theo phòng ban**: tổng tài liệu, đã publish, chờ duyệt, số tài liệu mật, job đang chạy, job lỗi.

### Người dùng (`users.py`)
- Tab mới **"Phòng ban & Khu"**: thêm/sửa phòng ban (mã, tên, lĩnh vực, khu mặc định) và khu/site — không cần sửa code.
- Tạo/sửa user: gán **phòng ban + khu + mức mật** trực tiếp.

### Chat — trích dẫn nguồn (`rag/service.py`)
- Mỗi nguồn trích dẫn nay kèm: tên file gốc, **DocID**, **version**, **phòng ban**, **khu** → người dùng truy ngược tài liệu gốc dễ dàng.

---

## 4. VẬN HÀNH ĐỊNH KỲ (script mới)

### 4.1 Đối soát SQL ↔ Qdrant (P1.7)
Mặc định **DRY-RUN** (chỉ báo cáo). Thêm `--fix` để dọn thật.

```powershell
$env:PYTHONPATH="src"; python scripts\danger_ops\reconcile_sql_qdrant.py
$env:PYTHONPATH="src"; python scripts\danger_ops\reconcile_sql_qdrant.py --fix
```
Xử lý: (1) xoá **vector mồ côi** (doc_id còn trong Qdrant nhưng đã mất ở SQL); (2) hard-delete tài liệu **kẹt trạng thái "deleting"** quá lâu (mặc định >6h, đổi bằng `--stuck-hours`).

> Khuyến nghị: chạy DRY-RUN hằng tuần, xem báo cáo rồi mới `--fix`.

### 4.2 Backup tự động (P1.8)
```powershell
$env:PYTHONPATH="src"; python scripts\ops\backup_system.py --sql-dir "D:\Backups" --keep-days 14
$env:PYTHONPATH="src"; python scripts\ops\backup_system.py --skip-qdrant   # chỉ backup SQL
```
- SQL: `BACKUP DATABASE` (full) + `BACKUP LOG` (nếu recovery model = FULL). **Lưu ý:** đường dẫn `--sql-dir` phải nằm trên **máy chạy SQL Server** và tài khoản SQL có quyền ghi.
- Qdrant: tạo **snapshot** collection.
- Tự dọn backup cũ hơn `--keep-days`.
- **Lịch định kỳ:** tạo Task trong **Windows Task Scheduler** chạy hằng ngày (full 1 lần/ngày, log nhiều lần/ngày).
- **Kiểm thử restore:** định kỳ phục hồi backup lên một DB tạm (`Mech_Chatbot_DB_restore_test`) để chắc backup dùng được — xem docstring trong script.

---

## 5. RE-EMBED TÀI LIỆU CŨ (để có `site` trong vector)
Tài liệu ingest trước P1 chưa có `site` trong payload Qdrant. Sau khi chạy migration:
- Cách an toàn: xoá rồi ingest lại từng tài liệu qua UI, **hoặc** dùng `scripts/danger_ops/nap_them_file.py` (KHÔNG dùng `wipe_and_reingest.py` vì nó xoá sạch collection).
- Trước khi re-embed xong: tài liệu cũ vẫn tìm được (nhờ điều kiện "site rỗng"), nhưng **chưa được bảo vệ theo khu**.

---

## 6. THỨ TỰ TRIỂN KHAI ĐỀ NGHỊ
1. Backup hiện trạng (`backup_system.py`) trước khi đổi gì.
2. Chạy migration P1 (`-I`).
3. Khởi động lại Streamlit + RAG worker/server.
4. Vào **Người dùng → Phòng ban & Khu** kiểm tra danh mục đã seed đúng; chỉnh khu mặc định nếu cần.
5. Gán **khu + mức mật** cho từng user.
6. (Khi rảnh) re-embed tài liệu cũ để siết RBAC theo khu.
7. Lên lịch `reconcile` (hằng tuần) + `backup` (hằng ngày) qua Task Scheduler.

---

## 7. KIỂM THỬ ĐỀ NGHỊ (P1)
- [ ] Chạy migration P1 → có bảng `Departments`, `Sites`, `UserSites`; `IngestionJobs` có cột `Priority`.
- [ ] UI **Phòng ban & Khu**: thêm 1 khu mới + 1 phòng mới → lưu OK, hiện trong bảng.
- [ ] Gán user tổ Hàn chỉ khu `XUONG_CO_KHI`, mức `internal`.
- [ ] User đó hỏi tài liệu thuộc khu `VP_KE_TOAN` → **không** truy xuất được.
- [ ] Ingest 1 tài liệu mới của Ke_Toan → `TaiLieu.Site = VP_KE_TOAN`, payload Qdrant có `metadata.site`.
- [ ] Kho tài liệu: lọc theo phòng + khu cho kết quả đúng; thẻ hiện Khu/Mức mật.
- [ ] Hàng đợi: đặt ưu tiên 1 job → job lên đầu; huỷ 1 job lỗi → chuyển `rejected`; ETA hiển thị hợp lý.
- [ ] Dashboard: bảng theo phòng ban khớp số liệu thực.
- [ ] Chat: câu trả lời có trích dẫn kèm DocID + khu.
- [ ] `reconcile_sql_qdrant.py` (dry-run) chạy không lỗi, báo cáo đúng.
- [ ] `backup_system.py --skip-qdrant` tạo được file `.bak`.

---

## 8. ĐÃ BỎ KHỎI GÓI THEO YÊU CẦU
- `.env` (API key thật) và thư mục môi trường `chat_env`.
