# CLAUDE.md — ChatBotProject (Mech Chatbot, RAG đa phòng ban)

Bàn giao từ Codex ngày 2026-11-03. Nguồn: `~/.codex/memories_v2/`, phiên Codex, `reports/rag-information-first-gd1/README.md`.

## Quy tắc làm việc (từ người dùng)

- Trả lời bằng tiếng Việt, ngắn gọn, dễ hiểu. Không dùng icon/emoji trong tin nhắn và trong code.
- Không overengineering, không kiểm kê rộng, không chạy test quá rộng. Sửa đúng nguyên nhân gốc, test focused, dùng quy trình phát hành sẵn có.
- Tách rõ: code xong / test focused / deploy / kích hoạt / chất lượng ngữ nghĩa / nghiệm thu nghiệp vụ. Bằng chứng demo, fixture, synthetic, QA không phải nghiệm thu nghiệp vụ.
- Commit, ký bundle, push, migration production, restart service, cutover: chỉ khi người dùng duyệt riêng từng lần.
- Báo cáo quản lý: nêu kết quả trước, rồi số đo và giới hạn bằng chứng; kèm link file local.
- Không bịa dữ liệu nguồn còn thiếu (Inox 201/304, bản vẽ `8.1.06524.290`, BOM child/leaf, tree-completeness).

## Checkout này là bản đang phục vụ production

- `C:\Users\bao.nguyen\Documents\ChatBotProject` chạy production qua Windows service (App/RAG/Worker + Cloudflared, domain `mechraglab.click`). HEAD detached tại `1c0a353` (GD8 + bản sửa QA web, phát hành 03/11), schema V0050, bundle v8 `9fcebe68`. Bản GD8 trước đó có tag `release/gd8-20261103`.
- Không sửa file tracked ở đây: checkout phải sạch thì lần deploy sau mới qua preflight. Làm việc trong worktree rồi phát hành.
- `git stash@{0}` ở checkout này giữ WIP cũ trước GD8 (57/64 file trùng candidate); chưa xóa.

## RAG "information-first" GD1–GD8: đã phát hành 03/11, chưa nghiệm thu nghiệp vụ

- Bảng tiến độ và biên nhận phát hành: `reports/rag-information-first-gd1/README.md` (mục GD8.5 cuối file).
- Plan master gốc: `~/.codex/attachments/e69707e9-300e-4aa1-8206-d426b3f2c9f9/pasted-text-1.txt`.
- Còn mở: kiểm trình duyệt sau phát hành (login, chat có nguồn, comprehensive dừng/tiếp tục, lịch sử); B1 thiếu nguồn nhiều phiên bản thật và trạng thái áp dụng thực tế; ứng viên mâu thuẫn Inox 201/304 (Doc60, mã 8.3.06316.010) chưa đưa vào rubric; B2 baseline chưa đo.

## Cách phát hành (đã dùng cho 8118dc0 và 85e527c)

- Bộ script: worktree `gd8-candidate-20261103`, thư mục `.local/runs/gd8-deploy-20261103/` (sign_release, prepare_packet, make_manifest, cutover.ps1, launch.ps1, helper, helper_sql). Sao chép thư mục, đổi OLD/NEW/WORK/PACKET/RELEASE, sửa phần migration trong helper_sql.
- Claude chạy được: ký bundle, đóng packet, tạo manifest. Người dùng chạy `launch.ps1` trong pwsh (PowerShell 7) quyền Administrator.
- Trước khi đưa lệnh: chạy lại toàn bộ kiểm đầu vào của helper ở chế độ chỉ đọc. GD8 mất 3 lượt vì sai đường dẫn file hỗ trợ và vì kiểm dữ liệu bắt nhầm file seed tracked.
- Drain từ chối khi worker đang chạy vòng đồng bộ Qdrant (mỗi 10 phút, khoảng 75 giây). Sau khi start service đợi khoảng 150 giây rồi mới chạy.
- Rollback không xóa `reports/<release>/release` trong checkout production; phải chuyển thư mục đó đi trước lần chạy lại.
- Claude không đăng nhập production bằng mật khẩu; smoke có đăng nhập do người dùng tự làm.
- Hash file đọc từ đĩa phải chuẩn hóa CRLF -> LF trước khi so (checkout production ra CRLF). Cửa sổ pwsh có thể treo ở bước in kết quả do QuickEdit: kiểm `result.json` trong `C:\ProgramData\MechRAGServicesudit-deploy-*` thay vì nhìn màn hình.

## GitHub (giabao2605/ChatbotProject, PUBLIC)

- `main` = production + commit docs. Còn 3 nhánh remote cũ chưa merge (mỗi nhánh 1 commit, 19/09): `codex/advanced-rag-offline-20260909`, `codex/offline-math-review-prep`, `codex/query-post-pilot-prep-20260905`.
- Repo public: không commit trích đoạn nguồn nghiệp vụ; `.gitignore` đã chặn phần lớn `reports/`. Pre-commit hook ECC quét secret; không bypass, sửa cho đúng.

## Việc khác còn treo

- (Đã phát hành 03/11 trong `1c0a353`) Public-web QA 31/10: 12 lỗi, bản sửa ở worktree `.codex\worktrees\public-web-qa-fixes` (nhánh `codex/public-web-qa-fixes`), chưa commit/deploy. Còn kiểm tessdata dưới identity worker thật.
- Nhiều worktree cũ dưới `.codex\worktrees\` và `.local\worktrees\`; chỉ dọn khi người dùng yêu cầu.

## Lệnh

- Python env: `chat_env\Scripts\python.exe` (Windows). Test focused: `chat_env/Scripts/pytest.exe -q backend/tests/unit/<file>.py`.
- Frontend (`frontend/`): `npm test`, `npm run build` (vue-tsc + Vite).
- Launcher: `run_server.py` (RAG API), `run_worker.py` (ingestion worker). Vận hành service: `docs/windows-service-operations.md`.
- Layout: `backend/src/mech_chatbot` (rag/, api/, db/), `backend/tests`, `frontend/` (Vue), `database/migrations` (V00xx), `scripts/`.
- Issue tracker: GitHub Issues qua `gh` (xem `docs/agents/`).
