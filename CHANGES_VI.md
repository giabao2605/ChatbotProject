# Mech Chatbot — Các file đã sửa theo Kế hoạch hoàn thiện (Vue 3 + FastAPI)

Giải nén gói này **đè trực tiếp lên gốc project** (cùng cấu trúc thư mục). Chỉ gồm
những file đã tạo/sửa. Có một số việc **xóa file** mà gói ghi-đè không thể diễn tả —
xem mục "CẦN XÓA THỦ CÔNG" bên dưới.

## 1. Frontend Vue 3 — Port toàn bộ UI vận hành (P5)

### Nền tảng dùng chung
- `web-ui/src/components/ResourcePage.vue` — khung bảng dữ liệu tái sử dụng (toolbar,
  bộ lọc, hành động theo dòng, form tạo mới, phân trang, refresh).
- `web-ui/src/components/StatView.vue` — khung hiển thị endpoint dạng object (thẻ số
  liệu + khối JSON + toolbar).
- `web-ui/src/utils/rows.ts` — helper đọc field cho cả row dạng dict lẫn dạng mảng vị trí.
- `web-ui/src/types.ts` — bổ sung type cho ResourcePage/StatView.
- `web-ui/src/api/client.ts` — viết lại: các verb chung (apiGet/apiSend/apiUpload) +
  helper auth (refreshSession, updatePreferredLanguage, CSRF token), xử lý 204/empty.
- `web-ui/src/styles.css` — thêm class tiện ích cho các trang vận hành.

### Các view thật (thay PlaceholderView)
DocumentsView, UploadView, QueueView, ReviewView, AccessView, UsersView, OrgView,
GlossaryView, MaterialsView, SettingsView, FeedbackView, AuditView, LifecycleView,
RegressionView, QualityView, AnalyticsView, ObservabilityView, HelpView.

### Định tuyến / khởi tạo
- `web-ui/src/router.ts` — thay PlaceholderView bằng view thật; thêm route `/org`,
  `/regression`, `/quality`, `/upload`.
- `web-ui/src/main.ts` — đăng ký component PrimeVue còn thiếu (Dialog) + khởi tạo i18n.
- `web-ui/src/App.vue` — nút chuyển ngôn ngữ, nhãn điều hướng dùng i18n, thêm mục nav mới.

## 2. i18n Vi/En (P3)
- `web-ui/src/i18n/messages.ts`, `web-ui/src/i18n/index.ts` — i18n phản ứng, không cần
  thư viện ngoài (mạng sandbox tắt nên không cài được vue-i18n).
- `web-ui/src/stores/auth.ts` — đồng bộ locale với `preferred_language`, gọi
  `PATCH /api/auth/me/preferences` khi đổi ngôn ngữ, và tự refresh phiên.

## 3. Backend FastAPI
- `src/mech_chatbot/api/app_server.py`:
  - **Thêm `POST /api/auth/refresh`** — xác thực cookie phiên + CSRF, xoay token phiên mới.
  - **Thêm `POST /api/documents/upload`** — nhận multipart (`file` + `thu_muc` +
    domain/security_level/cong_doan/site tùy chọn), kiểm tra đuôi & dung lượng (≤100MB),
    lưu vào `data/raw/Uploads/<phòng ban>/`, tạo ingestion job (pending). Yêu cầu vai trò
    uploader/reviewer/admin.

## 4. Test
- `tests/unit/test_retrieval_filters.py` — đổi tên `test_chitchat_skips_strict_part_id`
  → `test_empty_part_ids_skips_strict_part_id`.
- `web-ui/src/__tests__/i18n.test.ts`, `web-ui/src/__tests__/rows.test.ts`.

## 5. Dọn dẹp P7
- `requirements.txt` — bỏ `streamlit`.
- `docker/docker-compose.yml` — bỏ service `streamlit` (và cập nhật comment header).

---

## CẦN XÓA THỦ CÔNG (gói ghi-đè không xóa file được)
Sau khi UI Vue đã chạy đủ, hãy xóa các mục legacy sau ở project gốc:
- `run.py`
- `src/mech_chatbot/ui/`  (toàn bộ thư mục Streamlit)
- `components/liquid_login/`
- `chat-ui/`  (thư mục Next.js cũ)
- `web-ui/src/views/PlaceholderView.vue`  (không còn được dùng)

KHÔNG đụng tới: `chat_env/`, `web-ui/node_modules/`, `chat-ui/node_modules/`, `chat-ui/.next/`.

## LƯU Ý VẬN HÀNH
- Route `POST /api/documents/upload` cần thư mục `data/raw/Uploads/` (tự tạo khi upload)
  và cần **ingestion worker đang chạy** để xử lý job pending.
- Frontend đã pass typecheck `vue-tsc --noEmit` (exit 0).
- Vitest KHÔNG chạy được trong sandbox do lỗi optional-deps của rollup
  (thiếu `@rollup/rollup-linux-x64-gnu`, mạng tắt nên không `npm i` lại được).
  Trên máy bạn chỉ cần: `cd web-ui && npm i` rồi `npm test`.

## VIỆC CÒN LẠI / TÙY CHỌN (chưa làm trong gói này)
- Test orchestration chat cho `POST /api/chat/message` (cần môi trường chạy backend).
- Test frontend route guard / auth store / CSRF / validate upload sâu hơn.
- (Nhỏ) Deprecate `allowed_departments`, `max_security_level` trong `ChatRequest`;
  cấu hình `manualChunks` tách vendor trong vite; rà soát `npm audit`.
