# Rà soát dependency database cho UI tài liệu và từ điển

Ngày rà soát: 2026-07-11.

## Kết luận

Không drop bảng nào trong thay đổi hợp nhất UI này. Các bảng liên quan đều còn dependency runtime hoặc có ý nghĩa dữ liệu riêng biệt.

| Bảng | Dependency đang dùng | Quyết định |
|---|---|---|
| `TaiLieu` | Kho tài liệu, lifecycle, publication contract, serving gate, ingest metadata | Giữ |
| `PhongBanChiaSe` | RBAC theo phòng ban và publish contract | Giữ |
| `IngestionJobs` | Queue, review, dashboard và ingest quality gate | Giữ |
| `MaterialDictionary` | Material repository và material registry lúc ingest | Giữ |
| `MaterialSynonym` | Chuẩn hóa từ đồng nghĩa vật tư | Giữ |
| `DomainGlossary` | Glossary repository và mở rộng truy vấn theo domain | Giữ |
| `DocQualityScore` | Điểm chất lượng sau sử dụng dựa trên feedback | Giữ; không trùng với điểm chất lượng ingest trong `TaiLieu`/`IngestionJobs` |

## Điều kiện bắt buộc trước một migration drop trong tương lai

1. Không còn truy vấn đọc/ghi trong source, API, worker, job, report hoặc script vận hành.
2. Không còn foreign key, view, index, trigger hay dữ liệu cần bảo tồn.
3. Có migration chuyển dữ liệu, kiểm tra rollback và ít nhất một phiên bản deprecation.
4. Clean migration và integration tests phải chạy thành công trước khi áp dụng vào database vận hành.
