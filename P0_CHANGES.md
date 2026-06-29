# P0 — Metadata tổng quát đa phòng ban (đã triển khai)

Mục tiêu: bỏ phụ thuộc mô hình dữ liệu "thiên cơ khí", cho phép MỌI phòng ban
nhập / sửa / hiển thị metadata phù hợp với tài liệu của họ (kế toán, hành chính,
ISO, HSE, nhân sự...). Nhập được ngay lúc Upload, không phụ thuộc 100% vào AI.

## 1) Cơ sở dữ liệu
- Migration mới: `database/migrations/V0004__add_common_document_metadata.sql`
  (idempotent, ghi version vào `_SchemaVersions`).
- Cùng nội dung đã được thêm vào `database/schema/01_baseline.sql` (cài mới cũng có).
- Cột mới trên `dbo.TaiLieu`: Title, Summary, Tags, DocNumber, IssuedDate,
  EffectiveDate, ExpiryDate, ReviewDate, OwnerSigner, DocLanguage, EffectiveStatus
  (+ 2 index: DocNumber, EffectiveStatus/ExpiryDate).
- Cột mới trên `dbo.IngestionJobs`: UploadMetaJson (mang metadata nhập lúc upload
  xuống worker).
- Tận dụng bảng `dbo.DocumentAttributes` (đã có sẵn) cho trường ĐẶC THÙ theo domain.

### Cách chạy migration trên DB hiện có
```
sqlcmd -S <server> -d Mech_Chatbot_DB -E -I -i database/migrations/V0004__add_common_document_metadata.sql
```

## 2) Backend (`src/mech_chatbot/db/repository.py`)
- `create_ingestion_job(..., upload_meta=None)`: lưu metadata upload vào UploadMetaJson.
- `_get_or_create_doc(...)`: đọc UploadMetaJson và ÁP các trường common xuống TaiLieu +
  ghi trường đặc thù vào DocumentAttributes (ExtractedBy='manual').
- Hàm mới: `get_document_metadata`, `update_document_common_metadata`,
  `get_document_attributes`, `set_document_attributes`, `_apply_upload_meta_to_doc`.
- Đồng bộ nhẹ một số trường (title/doc_number/tags/effective_status) xuống Qdrant payload.

## 3) Giao diện
- Module mới `src/mech_chatbot/ui/metadata_forms.py`: form ĐỘNG theo domain
  (common fields + trường riêng cho tabular/generic). Ngày nhập dạng YYYY-MM-DD,
  cho phép bỏ trống.
- `ui/pages/upload.py`: thêm mục "Thông tin tài liệu (metadata)" — nhập lúc upload.
- `ui/pages/documents.py` (Kho tài liệu): form quản trị nay sửa được metadata tổng quát.
- `ui/pages/admin.py` (Duyệt): reviewer kiểm tra / bổ sung metadata trước khi publish.

## Tương thích ngược
- Tất cả cột mới đều NULL được; tài liệu cũ không bị ảnh hưởng.
- Mọi tham số mới đều optional; luồng cũ chạy bình thường nếu không nhập metadata.
