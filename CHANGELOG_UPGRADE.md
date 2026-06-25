# CHANGELOG NÂNG CẤP — Hoàn tất P0 (đa phòng ban)

> Bản này **hoàn tất nốt P0** dựa trên phần bạn đã làm dở. Mình chỉ sửa code; **chưa chạy thử trên SQL Server/Qdrant thật** (sandbox không có DB/Qdrant/API key của bạn). Toàn bộ file Python đã được `py_compile` không lỗi cú pháp.

## 1. Những gì BẠN đã làm sẵn (mình giữ nguyên)
- `ingestion/domain_registry.py`: cấu hình domain (co_khi / ky_thuat / ke_toan / nhan_su / chung).
- Schema: cột `TaiLieu.Domain/SecurityLevel/Site`, bảng `DocumentAttributes`, `UserSecurityClearance` (init idempotent).
- `document_classifier.py`: phân loại 2 tầng (domain → LLM theo domain).
- `pdf_processor.py`: tách `quality_mechanical` / `quality_generic`, `calculate_quality_status(report, domain)`.
- `auth/service.py`: khoá đăng nhập + rate-limit (5 lần sai / 10 phút).

## 2. Những gì mình LÀM NỐT trong bản này

### 2.1. Chấm điểm chất lượng đúng theo domain *(sửa lỗi)*
- **Vấn đề:** 2 chỗ gọi `calculate_quality_status(report)` **không truyền `domain`** → mặc định `'co_khi'` → tài liệu kế toán/nhân sự bị chấm theo thang cơ khí và bị trừ điểm "thiếu thuộc tính kỹ thuật".
- **Sửa:** cả 2 chỗ (PDF & file) nay gọi `calculate_quality_status(report, domain)`.
- File: `ingestion/pdf_processor.py` (≈ dòng 1122, 1324).

### 2.2. Router trích xuất theo domain
- **Vấn đề:** mọi tài liệu đều chạy `extract_mechanical_attributes` (regex cơ khí) → sai cho tài liệu hành chính.
- **Sửa:** chỉ domain `mechanical` (co_khi/ky_thuat) chạy regex cơ khí + lưu `TechnicalAttributes`. Domain khác dùng `generic_extractors.extract_generic_attributes()` và lưu vào `DocumentAttributes`.
- File mới: `ingestion/generic_extractors.py`.
- File: `ingestion/pdf_processor.py` (≈ dòng 926–942), `db/repository.py` (`save_document_attributes`).

### 2.3. RBAC 2 chiều: phòng ban × mức mật *(bảo mật)*
- **Vấn đề:** filter Qdrant chỉ lọc `phong_ban_quyen`, **chưa lọc theo mức mật** → người tổ Hàn có thể truy vấn tài liệu `confidential` của HR/Kế toán nếu lọt phòng ban.
- **Sửa:**
  - `auth/service.py`: khi đăng nhập, nạp `MaxLevel` từ `UserSecurityClearance` → trả về `max_security_level` trong thông tin user.
  - `rag/service.py`: `create_rbac_filter(..., max_security_level)` thêm điều kiện `metadata.security_level` (user chỉ thấy mức ≤ clearance). Dùng thứ tự `public < internal < confidential`.
  - Luồng tham số `max_security_level` xuyên suốt: UI (`chatbot.py`) → worker (`rag_worker.py`) / API (`rag_server.py`) → `chat_with_rag` → `extract_search_intent` → `create_rbac_filter`.
- **Tương thích ngược:** vector cũ CHƯA có `security_level` trong payload sẽ được coi là hợp lệ (điều kiện `IsEmpty`), nên **search hiện tại không bị chặn**. Để siết hoàn toàn với tài liệu cũ, cần **re-embed** (xem mục 4).

### 2.4. Route Vision theo nguồn text *(tiết kiệm chi phí)*
- **Sửa:** trang có lớp text dày (`is_text_heavy`, >1500 ký tự) thuộc domain **phi cơ khí** sẽ đọc thẳng text layer, **không đẩy GPT Vision**. Bản vẽ cơ khí vẫn luôn dùng Vision.
- File: `ingestion/pdf_processor.py` (≈ dòng 779–782).

### 2.5. Audit truy cập tài liệu mật
- **Sửa:** mỗi lần câu trả lời dùng tài liệu `confidential`, ghi cảnh báo vào log (`[audit][confidential] ...`).
- File: `rag/service.py` (sau `build_source_citations`).
- *Ghi chú:* đây là audit mức log. Audit ghi DB kèm **username** cần luồn thêm `username` vào `chat_with_rag` — để sang P1 (việc nhỏ).

### 2.6. Script vận hành (mới)
- `database/migrations/p0_backfill_domain_security.sql`: backfill `Domain/SecurityLevel` cho tài liệu cũ + tạo clearance mặc định cho mọi user (idempotent).
- `database/migrations/p0_harden_seed_accounts.sql`: vô hiệu hoá tài khoản seed (`admin/viewer1/uploader1/reviewer1`) **sau khi** đã có admin thật.

## 3. CÁC BƯỚC BẠN CẦN CHẠY (theo thứ tự)

1. **Backup trước:** SQL Server (full backup) + snapshot collection Qdrant `TaiLieuKyThuat_v2`.
2. **Cập nhật schema:** chạy lại `database/init/Mech_Chatbot_DB.sql` (đã idempotent — chỉ thêm phần thiếu, không xoá dữ liệu).
3. **Backfill dữ liệu cũ:** chạy `database/migrations/p0_backfill_domain_security.sql`. Mở phần comment cuối file để nâng clearance `confidential` cho đúng người (kế toán/HR).
4. **Cài lại phụ thuộc (nếu cần):** `pip install -r requirements.txt`.
5. **Re-embed tài liệu cũ** để vector mang `domain` + `security_level` (BẮT BUỘC nếu muốn siết mức mật trên tài liệu đã ingest):
   - Cách an toàn: xoá rồi ingest lại từng tài liệu qua UI, **hoặc** viết script đọc danh sách `TaiLieu` và gọi lại `process_and_ingest_*`.
   - Nếu chưa re-embed: tài liệu cũ vẫn tìm được (do điều kiện `IsEmpty`), nhưng **chưa được bảo vệ theo mức mật**.
6. **Tạo admin thật** rồi chạy `p0_harden_seed_accounts.sql`. Đổi toàn bộ mật khẩu mặc định `Admin@123`.
7. **Khởi động lại** Streamlit + RAG worker/server.

## 4. KIỂM THỬ ĐỀ NGHỊ (bạn tự chạy trên môi trường thật)
- [ ] Ingest 1 PDF kế toán/HR → kết quả `domain` đúng, `quality_status` không bị "fail" vì thiếu thuộc tính cơ khí.
- [ ] Ingest 1 file Word/Excel văn bản thuần → **không** gọi GPT Vision (xem log), vẫn ra chunk.
- [ ] User tổ Hàn (clearance `internal`) hỏi nội dung tài liệu HR `confidential` → **không** truy xuất được.
- [ ] User HR (clearance `confidential`) hỏi → truy xuất được; log xuất hiện dòng `[audit][confidential]`.
- [ ] Đăng nhập sai 5 lần → bị khoá tạm 5 phút.
- [ ] Xoá 1 tài liệu → không còn vector mồ côi; `DocumentAttributes` tự xoá theo CASCADE.

## 5. ĐÃ BỎ KHỎI GÓI THEO YÊU CẦU
- `.env` (API key thật) và thư mục môi trường `chat_env` (theo ghi chú của bạn — sẽ xử lý ở giai đoạn doanh nghiệp).

## 6. CÒN LẠI (đề xuất cho P1 — chưa làm trong bản này)
- Audit DB kèm username; quản lý phòng ban/site động trong UI; citation trong chat; lọc theo phòng/site; nâng cấp hàng đợi ingest; dashboard theo phòng; reconcile SQL↔Qdrant định kỳ; backup tự động.

---

# P1 — ĐA KHU / ĐA PHÒNG BAN, HÀNG ĐỢI & VẬN HÀNH (bản này)

> Chi tiết đầy đủ + hướng dẫn chạy: xem **`HUONG_DAN_P1.md`**.

## P1.1 Quản lý phòng ban/khu trong UI
- Tab mới **Người dùng → "Phòng ban & Khu"**: thêm/sửa phòng ban (mã, tên, lĩnh vực, khu mặc định) và khu/site — không cần sửa code.
- Bảng mới `Departments`, `Sites` (migration tự seed từ dữ liệu hiện có).

## P1.2 Lớp Khu (Site) + RBAC 3 chiều (Khu × Phòng × Mức mật)
- `ingestion/site_registry.py`: map phòng ban → khu (`resolve_site_by_department`).
- `auth/service.py`: user dict thêm `allowed_sites` (bảng `UserSites`; rỗng = không giới hạn khu).
- `rag/service.py`: thêm `_site_filter` vào RBAC; ingest ghi `metadata.site`; cột `TaiLieu.Site` được ghi khi tạo/cập nhật tài liệu.
- Gán quyền: **Người dùng → (mở user) → Phân quyền RBAC** (phòng ban + khu + mức mật).

## P1.3 Trích dẫn nguồn trong chat
- `build_source_citations`: mỗi nguồn kèm tên file, **DocID**, **version**, **phòng ban**, **khu**.

## P1.4 Lọc theo phòng ban & khu
- Kho tài liệu + Hàng đợi: thêm bộ lọc Phòng ban / Khu; tài liệu hiển thị Khu/Lĩnh vực/Mức mật.

## P1.5 Nâng cấp hàng đợi
- ETA (số job chờ × thời gian TB/job), **ưu tiên (Priority)**, **retry tay**, **huỷ job**; worker lấy job theo ưu tiên.
- `IngestionJobs` thêm cột `Priority`, `MaxPages`, `CanceledBy`, `CanceledAt`.

## P1.6 Dashboard theo phòng ban
- Bảng sức khoẻ theo từng phòng: tổng tài liệu, đã publish, chờ duyệt, số tài liệu mật, job đang chạy, job lỗi.

## P1.7 Đối soát SQL ↔ Qdrant
- `scripts/danger_ops/reconcile_sql_qdrant.py` (mặc định dry-run, `--fix` để dọn): xoá vector mồ côi + hard-delete tài liệu kẹt 'deleting'.

## P1.8 Backup tự động
- `scripts/ops/backup_system.py`: BACKUP DATABASE (full) + BACKUP LOG + snapshot Qdrant; tự dọn backup cũ; hướng dẫn restore-test trong docstring.

## CHẠY MIGRATION P1 (bắt buộc, làm trước)
```
sqlcmd -S localhost\SQLEXPRESS -d Mech_Chatbot_DB -I -i database\migrations\p1_multi_site_queue_admin.sql
```

## TƯƠNG THÍCH NGƯỢC
- Mọi thay đổi tương thích ngược: chưa chạy migration P1 thì hệ thống vẫn chạy như P0 (tính năng mới ẩn/không có dữ liệu).
