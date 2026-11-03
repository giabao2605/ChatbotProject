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

- `C:\Users\bao.nguyen\Documents\ChatBotProject` chạy production qua Windows service (App/RAG/Worker + Cloudflared, domain `mechraglab.click`). HEAD detached tại `8118dc0`, DB schema V0048.
- Đừng sửa `backend/src` ở đây. Các file test/frontend/migration đang dirty/untracked trong checkout này là phần sót lại của GD2–GD8 sau khi khôi phục production; không xóa, không reset.
- Làm việc mới trong worktree riêng.

## Công việc đang dở: RAG "information-first" GD1–GD8

- Candidate: worktree `C:\Users\bao.nguyen\.codex\worktrees\gd8-candidate-20261103\ChatBotProject`, nhánh `codex/gd8-acceptance-20261103` (đã commit `6dbe1dc` + `54d5ab5`, đã push GitHub, chưa merge main).
- Bảng tiến độ duy nhất: `reports/rag-information-first-gd1/README.md` trong nhánh đó. Cập nhật bảng đó, không tạo báo cáo mới mỗi lượt.
- Plan master gốc: `~/.codex/attachments/e69707e9-300e-4aa1-8206-d426b3f2c9f9/pasted-text-1.txt`; prompt GD8 cuối: `~/.codex/attachments/9f6cb287-f695-46ca-88b6-ddf84112c017/pasted-text-1.txt`.
- Trạng thái: GD2–GD7 hoàn tất candidate; runtime thật có giới hạn đã chạy cho B02/B05 (verified), B03 (partial). GD8.5 = NO-GO.
- Blocker còn mở (đều cần người dùng): nguồn B1 nhóm 1 (tài liệu thật có 2 phiên bản) và nhóm 3 (trạng thái áp dụng thực tế); nhóm 2 có ứng viên Inox 201/304 (Doc60, mã 8.3.06316.010) chờ duyệt đưa vào rubric; B2 baseline; ký bundle v8 (private key người dùng giữ); backup DB COPY_ONLY + cửa sổ bảo trì; migration V0049/V0050 + cutover.
- Bài học: service account không đọc được file module mới tạo -> production từng tự Stop. Trước start phải kiểm ACL đọc của 3 service account trên file source mới.

## GitHub (giabao2605/ChatbotProject, PUBLIC)

- `main` = `8118dc0` = production. Còn 3 nhánh remote cũ chưa merge (mỗi nhánh 1 commit, 19/09): `codex/advanced-rag-offline-20260909`, `codex/offline-math-review-prep`, `codex/query-post-pilot-prep-20260905`.
- Repo public: không commit trích đoạn nguồn nghiệp vụ; `.gitignore` đã chặn phần lớn `reports/`. Pre-commit hook ECC quét secret; không bypass, sửa cho đúng.

## Việc khác còn treo

- Public-web QA 31/10: 12 lỗi, bản sửa ở worktree `.codex\worktrees\public-web-qa-fixes` (nhánh `codex/public-web-qa-fixes`), chưa commit/deploy. Còn kiểm tessdata dưới identity worker thật.
- Nhiều worktree cũ dưới `.codex\worktrees\` và `.local\worktrees\`; chỉ dọn khi người dùng yêu cầu.

## Lệnh

- Python env: `chat_env\Scripts\python.exe` (Windows). Test focused: `chat_env/Scripts/pytest.exe -q backend/tests/unit/<file>.py`.
- Frontend (`frontend/`): `npm test`, `npm run build` (vue-tsc + Vite).
- Launcher: `run_server.py` (RAG API), `run_worker.py` (ingestion worker). Vận hành service: `docs/windows-service-operations.md`.
- Layout: `backend/src/mech_chatbot` (rag/, api/, db/), `backend/tests`, `frontend/` (Vue), `database/migrations` (V00xx), `scripts/`.
- Issue tracker: GitHub Issues qua `gh` (xem `docs/agents/`).
