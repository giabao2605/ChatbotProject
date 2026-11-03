# Tiến độ RAG — GD1 và GD2

Mốc baseline GD1: 2026-10-31, checkout `8118dc089dbd65eae2010e10883167f53b9d34e6`. Cập nhật GD8: 2026-11-03 tại candidate; hash mới nhất trong manifest.

**GD2 đã hoàn tất thay đổi code trong phạm vi danh tính nguồn và ngữ cảnh; chưa triển khai.** Kết quả candidate nằm ở phần GD2 bên dưới. Các mô tả baseline trong phần GD1 áp dụng cho checkout gốc, không phải code sau sửa.

GD1 đã tạo bộ chẩn đoán cục bộ và khóa tiêu chuẩn để triển khai các giai đoạn tiếp theo. **Chưa sửa hành vi ứng dụng, chưa nghiệm thu chatbot trên dữ liệu doanh nghiệp.** Baseline này chạy luồng điều khiển thật với ứng viên tìm kiếm và bản nháp trả lời được cấp sẵn; không đo chất lượng tìm kiếm hay khả năng hiểu của model thật.

Tài liệu đầu vào: [báo cáo nghiên cứu](../../docs/research/rag-information-first-2026-10-31.md). Các quyết định của kế hoạch được giữ nguyên: quyền lịch sử cấp riêng; trả theo từng nguồn; phân biệt quyền đọc với đối tượng áp dụng; thiếu trạng thái nghiệp vụ thì chưa xác nhận; quick không được hứa đã rà toàn kho.

## Bảng tiến độ chung

Đây là bảng theo dõi chung cho công việc này. Cập nhật bảng và bằng chứng tại đây; không tạo báo cáo tiến độ mới sau mỗi lượt chạy. `Hoàn tất GD1` nghĩa là hoàn tất khảo sát/chẩn đoán trong phạm vi nêu dưới đây, không có nghĩa các lỗi RAG đã được sửa.

| Task | Phụ trách | Phụ thuộc | Trạng thái | Bằng chứng / giới hạn |
|---|---|---|---|---|
| GD1.1 — Đối chiếu vấn đề với đường xử lý | Codex | Báo cáo + checkout | Hoàn tất | Ma trận vấn đề bên dưới phân biệt tái hiện, rủi ro và phụ thuộc nguồn; mỗi dòng có task sửa và ca kiểm |
| GD1.2 — Nguồn và đáp án chuẩn | Codex; chủ tài liệu xác nhận dữ liệu thật sau | GD1.1 | Hoàn tất bộ nguồn chẩn đoán; nghiệp vụ thật chưa xác nhận | 14 nguồn/24 ca development; 8 nguồn/12 ca holdout; trích đoạn và điều kiện ghi trong JSON, không lấy câu trả lời bot làm đáp án |
| GD1.3 — Baseline cố định | Codex | GD1.2 | Hoàn tất baseline cục bộ; baseline runtime chưa đo | 24 ca: 16 đạt điều kiện hẹp, 8 chưa đạt, 0 lỗi bộ đo; 2 kiểm tra tính toàn vẹn đạt; trace, cấu hình và hash kèm theo |
| GD1.4 — Tách tập sửa / nghiệm thu | Codex | GD1.2 | Hoàn tất | Holdout khác ID và nội dung nguồn, chưa chạy chatbot, chưa dùng chỉnh hệ thống; chỉ kiểm cấu trúc và đối chiếu oracle |
| B1 — Nguồn và đáp án nghiệp vụ thật | Codex được bao.nguyen ủy quyền03/11 | GD1.2 | Ba ca mục tiêu đã đạt bounded; B1 tổng thể chưa đạt | Source1eb82: B02/B05 verified, B03 đủ ý với bất định/partial; ba nhóm nguồn bắt buộc còn thiếu |
| B2 — Baseline retrieval/model/runtime | Codex | B1 và snapshot/quyền/runtime phù hợp | Có ba quan sát baseline lịch sử | Prompt đã đổi: không dùng đối chiếu cũ làm baseline matched hiện tại; không kết luận recall/p95/SLA/chi phí hoặc cải thiện toàn corpus |
| GD2.1 — Danh tính bằng chứng dùng chung | Codex | GD1 | Hoàn tất code và kiểm tra cục bộ | SourceLocator + hash trích đoạn xuyên retrieval, context, citation, SQL/history; structured hash cũ giữ nguyên |
| GD2.2 — Giữ đủ nguồn khi bỏ trùng | Codex | GD2.1 | Hoàn tất | D04/D13 thành ca hồi quy; khác tài liệu/revision/snapshot/vị trí còn nguyên; bản sao cùng bằng chứng chỉ giữ một |
| GD2.3 — Ngữ cảnh và parent expansion | Codex | GD2.1 | Hoàn tất trong dữ liệu đã nạp | Giữ metadata mục/bảng/hàng/cột/đơn vị/chú thích; chặn parent sai quyền, revision/snapshot và thời gian; V02 file gốc vẫn chưa đo |
| GD2.4 — Ingestion khi có lỗi đã chứng minh | Codex; chủ nguồn khi cần | Bằng chứng mất dữ liệu từ GD1 | Hoàn tất đánh giá điều kiện; chưa có căn cứ sửa ingestion | D18/D19 chỉ kiểm văn bản cấp sẵn; không tự kết luận OCR/chunk đúng, không reindex |
| GD3.1 — Ý định và disambiguation | Codex | GD2 | Hoàn tất code candidate | `query_scope` được nối vào resolver; general policy/comparison giữ các nguồn trước, single candidate mới chọn model |
| GD3.2 — Rewrite và glossary có ràng buộc | Codex | GD3.1 | Hoàn tất code candidate | Rewrite bị loại nếu mất mã, số, đơn vị, thời gian, phủ định hoặc tên file; glossary tối đa hai mở rộng; corrective rewrite dùng cùng guard |
| GD3.3 — Hybrid retrieval và độ phủ tài liệu | Codex | GD2 | Hoàn tất kiểm chứng candidate | Giữ hybrid hiện có; candidate diversity giới hạn chunk theo tài liệu/section để file dài không chiếm hết; không thêm nguồn ngoài candidate |
| GD3.4 — Chính sách hỏi lại và thiếu bằng chứng | Codex | GD3.1–3.3 | Hoàn tất code candidate | Câu hỏi rộng không hỏi tên file; câu cần chọn đối tượng vẫn hỏi; zero-hit nêu phạm vi truy xuất thay vì kết luận kho rỗng |
| GD4.1 — Quyền đọc lịch sử riêng | Codex | GD1 | Hoàn tất candidate | Role `document_history_reader` được seed/migration; không tự cấp cho admin/reviewer và không thay quyền đọc tài liệu |
| GD4.2 — Phục vụ bản cũ đúng chính sách | Codex | GD4.1, GD2 | Hoàn tất candidate | Retrieval, file original/page, citation và chat history dùng cùng gate; chỉ bản published/approved đã cũ hoặc hết hạn; draft/rejected/revoked và review-preview bypass bị chặn |
| GD4.3 — Dữ kiện nghiệp vụ gắn nguồn | Codex | GD2 | Hoàn tất candidate | `TextFact` giữ subject/attributes/applicability/status/time/confirmed_at trong payload source-bound; không tạo truth store độc lập |
| GD4.4 — Đề xuất và duyệt dữ kiện | Codex | GD4.3 | Hoàn tất candidate | Ingestion luôn pending và xóa `confirmed_at`; reviewer được gán và có quyền duyệt mới xác nhận; snapshot mới làm stale xác nhận cũ; màn hình duyệt hiển thị ngữ cảnh |
| GD4.5 — Tách quyền đọc và phạm vi áp dụng | Codex | GD4.1–4.4 | Hoàn tất candidate | Không suy trạng thái áp dụng từ upload/approval/version; thiếu status/time hiển thị chưa xác nhận; history role không bypass ACL/site/security |
| GD5.1 — Ý trả lời gắn bằng chứng | Codex | GD2–GD4 | Hoàn tất candidate; runtime giới hạn xem GD8 | Verifier một lượt nhận draft đã buffer, giữ từng ý có nguồn/quote hợp lệ và trả phần đúng khi loại ý sai; policy vẫn phân biệt full/partial/clarification/insufficient |
| GD5.2 — Phân loại đồng thuận và khác biệt | Codex | GD5.1 | Hoàn tất candidate; runtime giới hạn xem GD8 | Hợp đồng verifier phân loại `consensus`, `different_scope`, `conflict`, `replacement`; bảng tách tài liệu/phiên bản/đối tượng/điều kiện, không biểu quyết hoặc lấy trung bình |
| GD5.3 — Kiểm nguồn và kết luận | Codex | GD5.1–5.2 | Hoàn tất candidate; runtime giới hạn xem GD8 | Kiểm SourceID, quote literal, revision, số, đơn vị, phủ định, đối tượng, thời gian và trạng thái; verifier lỗi là lỗi kỹ thuật, verifier tắt giữ `unverified` |
| GD5.4 — Buffer trước khi phát | Codex | GD5.3 | Hoàn tất candidate; runtime giới hạn xem GD8 | Có nguồn truy xuất thì giữ toàn bộ draft đến sau post-check; lỗi verifier không phát `RagToken` và không biến thành zero-hit |
| GD5.5 — Cache và lịch sử theo hợp đồng an toàn | Codex | GD4, GD5.1–5.4 | Hoàn tất candidate; runtime giới hạn xem GD8 | Cache contract hiện tại `grounded-answer-v12-undivided-query-scope` và hash verifier gắn user/role/phòng ban/site/context/provider/snapshot; câu hỏi lịch sử/thời gian/cá nhân/toàn bộ bypass cache; history `verified-policy-v1` recheck actor, ACL, phiên bản và ngày |
| GD6.1 — Chế độ quick/comprehensive | Codex | GD2–GD5 | Hoàn tất candidate; runtime giới hạn xem GD8 | `api/search_contract.py`, hai API chat, `rag/search_mode.py`, `RagRequest`, command/HTTP adapter; kiểm input/câu rộng và tương thích quick |
| GD6.2 — Snapshot và rà lô | Codex + scan_executor + store_fix | GD6.1, GD2 identity | Hoàn tất candidate; runtime giới hạn xem GD8 | `rag/comprehensive_execution.py`, `db/repositories/comprehensive.py`; catalog đầy đủ, trang/ký tự liên tiếp, verifier coverage; không dùng top-k xác nhận đủ |
| GD6.3 — SQL và continuation | Codex + store_fix | GD6.2 | Hoàn tất candidate; runtime giới hạn xem GD8 | `rag/comprehensive.py`, V0050 scan/source/batch; transaction/CAS, replay đúng revision, đọc kết quả giới hạn trong SQL |
| GD6.4 — Ngân sách và dừng/tiếp tục | Codex + scan_executor | GD6.2–6.3 | Hoàn tất candidate; runtime giới hạn xem GD8 | `db/scan_connection.py`, executor, diagnostics/SSE; cursor trước generation, checkpoint/rollback, SQL timeout theo ngân sách còn lại |
| GD6.5 — Quyền và tính nhất quán | Codex + gd6_review | GD6.2–6.4 | Hoàn tất candidate; runtime giới hạn xem GD8 | Reauth trước lô/đọc/lưu; cursor ràng buộc owner/query/scope/snapshot; stale invalidation; final review do Codex thực hiện |
| GD7.1 — Trả lời theo nguồn | Codex + source_ui | GD2, GD4, GD5 | Hoàn tất candidate; runtime giới hạn xem GD8 | `AnswerSources.vue`, `types.ts`, metadata GD5 qua SSE/history; giữ từng identity/phiên bản/đoạn, quan hệ chỉ từ backend, thiếu dữ kiện ghi chưa xác nhận |
| GD7.2 — Chế độ và độ đầy đủ | Codex | GD6.1–6.4 | Hoàn tất candidate; runtime giới hạn xem GD8 | `ChatView.vue`, `client.ts`, `chatScan.ts`; quick mặc định, phản ánh backend promotion, đếm nguồn, tách done/snapshot_complete/results_pending |
| GD7.3 — Mở nguồn và truy cập | Codex + source_ui | GD2, GD4, GD7.1 | Hoàn tất candidate; runtime giới hạn xem GD8 | Giữ nguyên URL ghim; endpoint đọc hiện có, không review fallback; lỗi quyền/không khả dụng ẩn kết quả; Chromium fixture kiểm tải nguồn và bàn phím/focus |
| GD7.4 — Dừng, tiếp tục và replay | Codex + backend_contract; review gd7_review | GD6.3–6.5 | Hoàn tất candidate; runtime giới hạn xem GD8 | AbortSignal, payload câu hỏi/phạm vi ban đầu trong state message; cursor chỉ tiến khi nhận đủ revision, chống replay/gap/scan khác, stale/denied xóa kết quả; không thêm kho cursor |
| GD7.5 — Thông báo và lịch sử | Codex + backend_contract | GD5, GD7.1–7.4 | Hoàn tất candidate; runtime giới hạn xem GD8 | Optional verification_status/answer_outcome/source_groups; lịch sử kiểm quyền/pin hiện có, không lưu cursor; lỗi kỹ thuật tách thiếu bằng chứng, dữ liệu cũ không tự verified |
| GD8.1 — Candidate và khoảng trống | Codex | GD2–GD7, B1/B2 | Đóng băng1eb82; gói cuối đã đối chiếu | Hash nội dung không phải commit; nhánh codex/gd8-acceptance-20261103, chưa commit/ký |
| GD8.2 — Runtime cô lập | Codex | GD8.1, nguồn/actor thật | Ba lỗi yêu cầu đạt mẫu runtime bounded | B02/B05 verified; B03 đủ nội dung/partial có bất định, UI/source/history/logout đạt; B01/B04 đủ nội dung/partial; comprehensive cùng scan4/4, SQLrevision1/2 không nhân bản |
| GD8.3 — Nghiệm thu có nguồn chuẩn | Codex được ủy quyền | B1, GD8.2 | B02/B03/B05 đóng trong phạm vi; B1 còn thiếu nguồn | Root đối chiếu trực tiếp rubric không đổi; không nâng partial B03 thành verified hoặc đóng ba nguồn thiếu |
| GD8.4 — Blocker và review | Codex + business_sources; reviewer độc lập | Sửa atomic prompt/opaque-ID, focused/review đạt | 84verifier/91,84% module,31generation,27caller không đổi; không cộng nhóm trùng. Sol-high không high/medium; vận hành còn mở |
| GD8.5 — Chuẩn bị phát hành | Codex + release_review | GD8.1–8.4, B1/B2 | NO-GO; chưa xin cutover | Ba nhóm nguồn thiếu, token-service/backup và duyệt commit/ký/migration/cutover còn mở; production8118dc0/V0048 giữ nguyên |

## Gói bằng chứng và cách đọc kết quả

- [development.json](development.json): nguồn tổng hợp, câu hỏi, trích đoạn chuẩn, điều kiện áp dụng, bản nháp cấp sẵn và kỳ vọng cho từng ca D01–D24.
- [holdout.json](holdout.json): H01–H12, đáp án chuẩn chưa chạy. Mỗi `sources` là một snapshot riêng của ca đó, không phải toàn bộ nguồn trong file.
- [lock.json](lock.json): hash code backend, fixture dùng lại, bộ đo, cấu hình pytest và hai tập dữ liệu. Không đưa bí mật/cấu hình môi trường thật vào gói.
- [baseline.json](baseline.json): quan sát từng ca, nguồn đầu vào, nguồn thực sự được trích dẫn, ứng viên citation, bước trace, kết quả đối chiếu và phiên bản thư viện.
- [Bộ chạy](../../backend/tests/diagnostics/information_first_baseline.py): tái sử dụng `test_rag_pipeline_public_characterization.offline_pipeline` và executor hiện có. Tên file không bắt đầu bằng `test_` để các ca đỏ có chủ đích không tự lọt vào suite CI thông thường.

16 ca đạt chỉ chứng minh những điều kiện được ghi trong `expected`. Ví dụ D01–D03 chứng minh đường xử lý chấp nhận câu hỏi có/không tên khi đã được cấp đúng nguồn và bản nháp; **không chứng minh retrieval tìm được nguồn hoặc model hiểu cách diễn đạt**. D08 không chứng minh model tự phát hiện mâu thuẫn: bản nháp đối chiếu đã được cấp sẵn. Không dùng 16/24 làm điểm chất lượng RAG.

Các bản nháp sai ở D09/D10/D21 là phép thử chặn lỗi sau generation. Kết quả cho biết đường kiểm đã cho lọt bản nháp đó trong cấu hình verifier tắt; không chứng minh model thật sẽ sinh lỗi với tần suất nào. Trường `verifier_disabled` được giữ trong trace, không đổi thành đã xác minh.

## Ma trận vấn đề, nguyên nhân và công việc tiếp theo

Phân loại: **Tái hiện** = chạy code thật tại ranh giới ghi rõ; **Rủi ro** = điều kiện trong code/chưa đo trên kho thật; **Thiếu năng lực** = yêu cầu mới chưa có, không gọi là regression; **Chưa đo** = không suy ra từ bộ mô phỏng.

| Vấn đề trong báo cáo | Bước / phân loại và bằng chứng GD1 | Task xử lý | Điều kiện kiểm chứng sau sửa |
|---|---|---|---|
| §3.1, §7: hỏi không biết tên file | D01/D02/D03: cả ba đi tới `answered` với nguồn 101 cấp sẵn. Không có trường bắt nhập file trong request. Recall thật chưa đo | GD3.1–3.4 | Cặp có/không tên lấy đúng đoạn trên cùng kho và quyền; không hỏi tên file vô ích |
| §3.2: ý định rộng bị ép chọn tài liệu | **Tái hiện D05**: `_disambiguate` kết thúc dù `general_policy`, `need_disambiguation=false`; D06 có yêu cầu so sánh thì đi tiếp | GD3.1, GD3.4 | Tra cứu rộng trả riêng các nguồn trước; câu cần chọn đối tượng áp dụng vẫn hỏi đúng điều kiện thiếu |
| §3.3: bỏ trùng theo đầu đoạn | **Tái hiện D04**: `_retrieve` strict+broad nhận 111/112 nhưng chỉ giữ 111 vì cùng 200 ký tự đầu; 1/2 nguồn còn lại tại merge | GD2.1–2.2 | Giữ cả hai nguồn khác nhau; cùng nguồn được truy xuất hai lần chỉ giữ một; lan tới context/citation/history |
| §3.3, §6: citation mất phiên bản | **Rủi ro tái hiện có điều kiện D13**: `build_citation_list` gộp cùng DocID/page khác version, chỉ còn version 1. Chưa xác nhận DB thực tế có dùng chung DocID cho hai revision | GD2.1–2.2, GD7.3 | SourceLocator phân biệt nguồn/phiên bản/snapshot/vị trí; kiểm invariant DB trước khi quyết định chỗ sửa |
| §3.4, §7: nhiều nguồn, file dài lấn file ngắn | D07 giữ/trích hai nguồn giống nội dung trong nhánh chung. **Chưa đo V01**: ba nguồn với một file nhiều chunk, rerank và ngân sách context thật | GD3.3, GD5.1, GD7.1 | Nguồn liên quan đều có đại diện; nguồn gây nhiễu không được thêm cho đủ số; mỗi nguồn gắn ý trả lời |
| §3.4, §5.2: top-k không chứng minh đầy đủ | **Thiếu năng lực D23**: request chưa có `search_mode`, `include_history`, `continuation_cursor`. H12 đã khóa tiêu chuẩn; quét có tiếp tục chưa chạy | GD6.1–6.5, GD7.2/7.4 | Danh mục snapshot, kiểm từng lô, tiến độ/lỗi, dừng/tiếp tục; thay quyền/nguồn thì không nối hai snapshot |
| §3.5: lịch sử và hiệu lực tài liệu | **Thiếu năng lực D14**: có tên role lịch sử trong profile vẫn bị `document_not_servable`. D15 giữ chặn bản cũ cho viewer. D17 chặn hết hạn/tương lai/draft/thu hồi/ngày sai | GD4.1–4.2 | Quyền lịch sử được cấp riêng, không mặc định admin; cần thêm quyền đọc nguồn; không dùng review-preview để lách; thu hồi có hiệu lực mọi đường |
| §3.5, §5.3: approved/current không là đang triển khai | **GD5 candidate đã chặn D10**: draft khẳng định đang triển khai chỉ được nhận khi quote có trạng thái hoạt động; thiếu ngày/trạng thái vẫn giữ chưa xác nhận | GD4.3–4.5, GD5.3 | Chủ thể/trạng thái/thời gian có nguồn và xác nhận; thiếu thì trả phần kế hoạch có thật và phần chưa xác nhận; thay nguồn làm stale xác nhận |
| §3.6: heading/bảng/chú thích/đơn vị/OCR | D18 bảng văn bản giữ ca đêm/8 giờ/25000; D19 transcript scan giữ yêu cầu 2 giám sát. **Chưa đo V02**: file gốc qua OCR/chunk/parent | GD2.3–2.4 | Đối chiếu vị trí/ô/đơn vị/điều kiện trước và sau nạp; chỉ nạp lại file có bằng chứng bị mất; parent không vượt quyền/phiên bản |
| §3.7, §5.2: đúng ID nhưng sai dữ kiện/đối tượng | **GD5 candidate đã chặn D09**: verifier yêu cầu quote literal và đối chiếu subject/condition/time; ý sai đối tượng bị loại riêng, nguồn đúng vẫn được giữ | GD5.1–5.3 | Kiểm từng ý với đúng nguồn, đối tượng, thời gian, đơn vị, phủ định; khác phạm vi không gọi mâu thuẫn; không trung bình/biểu quyết/version chéo họ |
| §5.1, §7: diễn đạt lại, từ địa phương, hội thoại | D03 thiếu dấu và D20 alias đi qua executor với đúng nguồn sẵn. **Chưa đo V03**: rewrite/model/history thực tế | GD3.2 | Giữ câu gốc, mã/đối tượng/phủ định/thời gian/đơn vị; tối đa2 cách tìm trong budget; history chỉ giải tham chiếu, không làm bằng chứng |
| §5.4: tài liệu chèn chỉ dẫn | **GD5 candidate đã chặn D21**: quote chứa chỉ dẫn/mật khẩu giả không được đưa vào claim hoặc bảng nguồn; verifier lỗi kỹ thuật cũng không phát draft | GD5.3–5.4 | Tài liệu là dữ liệu; kiểm ý trả lời theo câu hỏi và nguồn hợp lệ; không thực thi chỉ dẫn trong tài liệu |
| §5.4, §7: thiếu đáp án / đủ một phần | D11 từ chối giá tiền không có, D12 phát phần nghỉ phép có nguồn; D24 từ chối khi retrieval rỗng. **Rủi ro lời văn D24**: thông báo suy rộng sang kho chưa có dữ liệu | GD3.4, GD5.1, GD7.5 | Trả phần có chứng cứ; phần thiếu nêu đúng phạm vi đã tìm; không suy ra kho rỗng chỉ từ zero hits |
| §5.4, §7: timeout bị hiểu thành không có dữ liệu | D22 xác nhận `RagFailed`, `RequestDeadlineExceeded`, không generation/answer; **Chưa đo V04** lỗi provider/extraction và SSE qua API | GD5.4, GD6.2/6.4, GD7.4 | Lỗi kỹ thuật riêng với thiếu bằng chứng; buffer đến kiểm xong; không phát nội dung chưa kiểm hoặc tuyên bố đủ khi có lỗi |
| §2, §7: đọc được không đồng nghĩa áp dụng | D16 xác nhận `department_denied`; D09 đối chứng nhân viên/cộng tác viên. **Chưa đo V05** user/site/security qua retrieval thật/API | GD4.5, GD5.5 | Tách ACL và đối tượng áp dụng; không lộ tên/số lượng nguồn bị cấm; quyền lịch sử không thay quyền tài liệu |
| §6–7: cache/history và thu hồi | **Chưa đo V06**: baseline tắt cache, không lưu chat SQL. Có invalidation trong version hiện có chưa phải bằng chứng mọi đường an toàn | GD4.2, GD5.5, GD7.4 | Cache gắn nguồn/quyền/user/phạm vi/thời gian/chế độ; tắt semantic reuse cho lịch sử/comprehensive bản đầu; đọc lại kiểm quyền |
| §6: hiển thị, mở đúng nguồn, client cũ | **Chưa đo V07**: chưa gọi HTTP/SSE/browser. D13 chỉ là builder citation, không phải click đúng file | GD7.1–7.5 | Từng tài liệu nói gì, phiên bản/điều kiện/trạng thái; mở đúng trang/ô; keyboard/screen reader; client cũ không bị gắn nhãn verified sai |
| §7: chất lượng, chi phí và phát hành | **Chưa đo V08**: recall thật/p95/chi phí=null; business_accepted=false | GD8.1–8.5 | Tập độc lập có mẫu số; recall≥95% và không giảm baseline; p95≤120% baseline cùng điều kiện; review, rollback, phê duyệt đúng bản và smoke trước tuyên bố triển khai |

Các V01–V08 là tiêu chí kiểm chứng còn thiếu, không phải ca đã chạy hoặc kết quả đạt. Không lấy test có sẵn, thông số top-k hay hash snapshot để thay chúng. Khi bắt đầu task tương ứng, chuyển tiêu chí thành ca tái hiện tại ranh giới thực, dùng lại fixture hiện có nếu phù hợp.

## Nguồn chuẩn và mức độc lập

Nguồn development gồm nghỉ phép, bản sao, mâu thuẫn, đối tượng gây nhiễu, kế hoạch thiếu thời gian, bản cũ, hai bản vẽ, đoạn injection giả, bảng phụ cấp, hai đoạn chung200 ký tự, alias và scan. Hầu hết là dữ liệu tổng hợp có ghi rõ xuất xứ; không phải chính sách công ty.

Alias dùng lại `data/crag_eval_v1/corpus/crag_eval_alias_v1.md`, chỉ nằm trong development. Scan dùng mẫu cục bộ `reports/remaining-fixes-20261027/ocr-samples/permit-scan.pdf`; đã nhìn bản render mới, đối chiếu dòng “Permit requires 2 supervisors before entry.”, ghi SHA256 file và transcript trong development. Không chạy OCR mới; D19 không chứng minh ingestion đúng. File PDF gốc không được thêm vào Git; transcript đã khóa đủ để chạy lại phép kiểm hẹp.

Holdout dùng bộ nội dung khác về công tác, đào tạo, áp suất và bảo trì; có tên/không tên/thiếu dấu, nguồn trùng, mâu thuẫn, nguồn sai đối tượng, thời gian thiếu, lịch sử, bảng và alias. Nó độc lập về dữ liệu với development, nhưng chưa được người nghiệp vụ xác nhận và không thay một tập nghiệm thu đủ mọi lớp quyền/SSE/UI. Không dùng holdout để chỉnh prompt/tham số. Khi nghiệm thu, người chấm đối chiếu từng ý với nguồn; từ khóa có mặt không đủ để kết luận đúng.

Mỗi ca có nguồn được xác định trước khi chạy, trích đoạn literal, điều kiện và kỳ vọng. Nguồn gây nhiễu có thể nằm trong ứng viên nhưng không tự trở thành nguồn đáp án; H06 chỉ bắt buộc nguồn 201, cho phép cite 204 khi giải thích đối tượng khác. Không cấm nêu giá trị sai đối tượng trong một câu đối chiếu đúng.

B1 cần chủ tài liệu cung cấp/xác nhận một mẫu được phép dùng, vị trí đáp án, danh sách nguồn liên quan, quan hệ phiên bản, người được đọc và điều kiện áp dụng. Trạng thái hoạt động thiếu giữ `unknown`. Không lấy ngày upload/duyệt hoặc dữ liệu demo 1.050 câu để tự xác nhận nghiệp vụ. Những nguồn thật chưa có mapping/duyệt đáng tin cậy không được đưa vào golden set.

## Snapshot, cấu hình và cách chạy lại

Baseline đóng băng ngày kiểm serving là 2026-10-31. Executor dùng scope HR/viewer/internal/HCM, helper không truyền identity tài khoản; probes file dùng profile giả 900001. Fixture cấp ứng viên, không thực thi bộ lọc ACL trong vector database. D16 kiểm hàm quyền file thật, không đại diện cho toàn bộ đường retrieval.

Cache, parent hydration, provider rerank, CRAG, query decomposition và evidence verifier tắt; strict answer bật; fixture top-k 5. Đây là cấu hình chẩn đoán, không phải cấu hình runtime đã xác nhận. SQL/Qdrant/provider được thay ở ranh giới theo fixture có sẵn; kết nối socket bị chặn trong ca kiểm. Log xuất chỉ gồm trace an toàn, ID giả và câu trả lời tổng hợp; prompt/raw runtime không được đưa vào gói. Thời gian từng ca là thời gian cục bộ, không phải latency sản phẩm.

Chỉ chạy lại khi cần tái hiện trên đúng snapshot; lệnh dưới ghi file mới, không đè baseline:

```powershell
$env:GD1_OUTPUT = '.local/gd1-replay.json'
& .\chat_env\Scripts\python.exe -m pytest backend/tests/diagnostics/information_first_baseline.py --tb=no --show-capture=no
Remove-Item Env:GD1_OUTPUT
```

Bộ chạy từ chối khi hash hoặc commit đổi và từ chối ghi đè output. Baseline gốc giữ nguyên; khi GD2 trở đi thay code, chuyển ca liên quan thành test hồi quy thường và ghi kết quả candidate riêng, không sửa oracle/lock gốc để biến đỏ thành xanh. Không cần chạy toàn suite cho thay đổi tài liệu này.

Lượt cuối: **8 failed, 18 passed** = 8 yêu cầu chưa đạt + 16 ca đạt điều kiện hẹp + 2 self-check. Exit pytest1 là dự kiến của chẩn đoán lỗi hiện tại, không phải CI xanh. Tám ca đỏ là D04/D05/D09/D10/D13/D14/D21/D23. Không bỏ ca đỏ khỏi mẫu số.

Lịch sử hiệu chỉnh bộ đo trước khi chốt: lượt đầu có 3 lỗi gọi API quyền do thiếu `strict_site_filter`; đã sửa bộ đo, không sửa app. Tiếp đó tách nguồn được cite thật khỏi danh sách ứng viên citation, siết D16/D22, thêm self-check grader và hash cấu hình. D10 cấm mệnh đề khẳng định thay vì cấm cụm “đang triển khai” để không loại lời phủ định đúng. Review oracle holdout sửa H06 và tiêu chí đối chiếu H09 trước khi chạy holdout; không thay nguồn/đáp án theo kết quả model. Log thử nằm trong `.local`, không có báo cáo tiến độ riêng.

Review độc lập đã kiểm bộ đo và oracle; các điểm sai nêu trên đã sửa. Chưa security-review toàn ứng dụng vì GD1 không thay code phục vụ. Không cài thư viện, không gọi provider thật, không đổi cấu hình runtime, không nạp lại kho, không migration, commit, push hoặc triển khai.

## Điểm bắt đầu giai đoạn sau

GD2 đã xử lý nguyên tắc danh tính nguồn từ D04 và invariant của D13, kiểm từ merge/context đến citation và SQL/history. GD3 đã xử lý D05 cùng các ràng buộc rewrite, độ phủ candidate và chính sách hỏi lại. GD4 nhận D14 và dữ kiện thiếu; GD5 nhận D09/D10/D21 và đã hoàn tất candidate theo bảng tracker. B1/B2 vẫn là phụ thuộc nghiệp vụ, không dùng kết quả tổng hợp để tuyên bố nghiệm thu. GD6–GD8 và nghiệm thu runtime vẫn chưa hoàn thành.


## GD2 — Kết quả triển khai cục bộ

Bằng chứng cố định: [gd2-result.json](gd2-result.json), gồm base commit, hash các file candidate, danh sách ca chạy và coverage. Candidate là working tree chưa commit; không dùng SHA nền để tuyên bố đó là bản đã triển khai. Bộ nguồn, lock và baseline GD1 giữ nguyên; holdout chưa chạy.

- Dùng danh tính nguồn chung gồm tài liệu, phiên bản, snapshot/hash khi có, vị trí và SHA-256 toàn trích đoạn. Không dùng 200 ký tự đầu hoặc chỉ DocID/trang để bỏ trùng. Thiếu pin vẫn là thiếu; hash trích đoạn không được coi là hash file đã xác minh.
- Áp dụng ở strict/broad/batched merge, RRF, corrective, query decomposition, rerank, late interaction, context và citation. Nhánh citation tính toán giữ riêng các đoạn cùng trang; ghép graph context phải cùng revision/snapshot. Không thay thuật toán tìm kiếm, chọn nguồn theo ý định hoặc ngân sách.
- Context giữ metadata mục, bảng/hàng/cột, ô, đơn vị và chú thích của chính đoạn đó. Parent expansion giữ quyền/site/security và revision/snapshot, kiểm serving/current và ngày hiệu lực/hết hạn; thiếu version không mở rộng.
- Citation và lịch sử SQL giữ danh tính đoạn trong cột JSON đã có, không cần migration. Page pin dùng để kiểm quyền vẫn được đối chiếu SQL; metadata snapshot bổ sung không thay được pin. Tham chiếu sai schema, sai quan hệ document/version/page, digest hoặc kích thước bị chặn. Đường đọc schema cũ vẫn được hỗ trợ.
- Mã dẫn cũ DdocPpage vẫn được hỗ trợ với các đoạn cùng revision; nếu cùng mã nhưng khác revision/snapshot thì từ chối gán nguồn mơ hồ. Thêm khóa hiển thị theo evidence_identity, không thiết kế lại UI. Hợp đồng structured citation/hash cũ không thay đổi.

**Kiểm tra:** 261 ca backend đạt, không lỗi/skip; coverage dòng thực thi backend thay đổi 96.86% (154/159). Có executor với nguồn/bản nháp cấp sẵn, SQL–HTTP history roundtrip, pin/quyền bị thu hồi, nguồn structured cũ và cache. Hai template Vue biên dịch được bằng compiler đã cài trong worktree repo-restructure. Vitest/typecheck/browser chưa chạy vì checkout hiện tại thiếu dependencies frontend; không cài thêm. Đây không phải E2E trình duyệt hay phép đo chất lượng model.

Các lỗi tái hiện đã có bước đỏ trước khi sửa: merge cùng đầu đoạn; nguồn khác revision/vị trí; parent khác snapshot/ngày sai; danh tính mất ở snapshot executor; history bỏ đoạn cùng trang; page pin bị hiểu nhầm thành snapshot pin; nhánh math bỏ đoạn; graph ghép khác snapshot; tham chiếu quá lớn chỉ bị từ chối khi đọc. Tự review đã kiểm code kết nối và các đường quyền; review độc lập không hoàn tất do agent hết quota, không ghi nhận như đã đạt.

**GD2.4:** chưa có bằng chứng GD1 rằng ingestion làm mất nội dung. Không sửa ingestion hoặc nạp lại tài liệu theo phỏng đoán. File gốc/OCR/chunk thật vẫn là V02 cần dữ liệu phù hợp ở bước nghiệm thu; không đánh dấu đã kiểm chất lượng nạp kho.

**Lưu ý vận hành:** cache câu trả lời đổi contract sang v9 để không tái sử dụng snapshot đã làm mất danh tính nguồn. Khóa shadow late-interaction cũ không còn khớp; cơ chế fallback khi thiếu coverage vẫn hoạt động, chưa dựng lại shadow index. Chưa đo ảnh hưởng latency runtime. Không đổi model, timeout, vector database, không reindex, commit, push hoặc triển khai. GD4 và GD5 đã có candidate riêng ở bên dưới; GD6–GD8 và B1/B2 chưa được coi là hoàn thành.

## GD3 — Kết quả triển khai candidate

GD3 đã xử lý bốn task trong phạm vi intent, rewrite, retrieval và chính sách hỏi lại; không thay quyền, lifecycle, lịch sử hoặc dữ kiện nghiệp vụ của GD4.

- `query_scope=general_policy` và `compare_candidates` không đi vào nhánh chọn một model. Nhánh `single_candidate` vẫn hỏi khi nhiều biến thể cơ khí không đủ điều kiện chọn.
- Rewrite hội thoại giữ câu gốc làm fallback. Mã, version, số đo, đơn vị, ngày/thời gian, phủ định và tên file bị mất trong rewrite thì không dùng bản rewrite. Corrective retrieval dùng cùng kiểm tra; glossary chỉ thêm tối đa hai cách diễn đạt.
- Candidate diversity vẫn chạy trước rerank, giới hạn số chunk theo tài liệu và section nhưng giữ revision/snapshot/chunk identity. Test mới chứng minh một file dài không chiếm hết candidate cap khi các tài liệu khác có ứng viên.
- Khi không có kết quả, thông báo nói rõ phạm vi tài liệu được phép truy xuất và hướng dẫn diễn đạt thêm; không yêu cầu người dùng biết tên file. Với câu hỏi chọn một đối tượng cụ thể, resolver vẫn trả bảng lựa chọn hoặc hỏi thêm điều kiện.

**Kiểm tra GD3:** 34 test focused đạt, gồm intent/rewrite, disambiguation, glossary, candidate diversity, corrective retrieval và empty-context policy. Không chạy full-suite, provider thật, recall production hoặc browser E2E. Candidate chưa commit/deploy; GD4 và GD5 được ghi ở các phần tiếp theo.

## GD4 — Kết quả triển khai candidate

GD4 đã khóa quyền lịch sử, vòng đời bản cũ và dữ kiện nghiệp vụ trong các đường đọc hiện có. Dữ kiện mới vẫn là đề xuất gắn với snapshot nguồn; chỉ người phụ trách đã được gán và có quyền duyệt mới tạo trạng thái xác nhận.

- `document_history_reader` là role riêng trong authorization, seed và migration V0049; không có grant mặc định và `admin` không được kế thừa role này.
- File original/page, citation, truy xuất RAG và đọc lại chat kiểm cùng điều kiện: published + approved, lifecycle hợp lệ, bản không hiện hành hoặc đã hết hạn; ACL phòng ban/site/security vẫn chạy sau history gate. Review preview tiếp tục là đường riêng, không dùng để lách quyền lịch sử.
- `TextFact` mở rộng source-bound context gồm subject, attributes, applicability, activity status, valid window và confirmation timestamp. Extraction ép `pending` và xóa confirmation cũ; approval ghi thời điểm xác nhận, reject xóa; snapshot nguồn mới làm approval cũ không còn hợp lệ.
- Màn hình `TechnicalFieldReview` hiển thị ngữ cảnh và nhãn chưa xác nhận, dùng lại API duyệt hiện có; không tạo kho sự thật độc lập hoặc suy phạm vi áp dụng từ metadata tài liệu.

**Kiểm tra GD4:** Bộ test focused cho authorization, file access, history SQL, text evidence, intent và structured evidence đạt; 1 test tùy chọn bỏ qua vì thiếu bộ mẫu bản vẽ. Compileall và `git diff --check` đạt. Frontend typecheck/build chưa chạy vì checkout không có `frontend/node_modules`; không cài dependency. Chưa chạy DB migration trên runtime, Qdrant/provider thật, browser E2E, recall/p95 hoặc deploy; đây là candidate local, không phải bằng chứng production.

## GD5 — Kết quả triển khai candidate

GD5 đã hoàn tất candidate kiểm từng kết luận; giữ nguyên policy full/partial/clarification/insufficient và không triển khai cơ chế rà toàn kho của GD6.

- Mỗi đoạn context, citation và merge dùng cùng `evidence_key`; câu trả lời có nguồn nhưng section không có giao với nội dung nguồn bị đánh dấu `claim_source_support`. Guard này chạy cả ở generation và claim repair nên bản sửa không thể giữ citation sai.
- Prompt yêu cầu trình bày từng tài liệu/phiên bản/đối tượng/điều kiện riêng, cảnh báo mâu thuẫn thật, phân biệt khác phạm vi và không chọn theo số đông, trung bình hoặc chỉ vì version cao hơn. Context vẫn giữ tiêu đề bảng, hàng/cột, đơn vị, chú thích và SourceID của đoạn.
- `EvidenceDecision.verification_status` phân biệt `verified`, `unavailable`, `unverified` và `deterministic`. Khi verifier tùy chọn bị tắt, diagnostics ghi `unverified`; không nâng trạng thái thành đã kiểm.
- Với câu hỏi có tài liệu truy xuất, stream được buffer đến khi kiểm số, đơn vị, mã, citation và hỗ trợ nguồn hoàn tất. Nếu không đạt, hệ thống thử đúng một lần claim repair có giới hạn; nếu vẫn lỗi thì trả trạng thái chưa đủ bằng chứng. Ngoại lệ kỹ thuật vẫn đi qua nhánh lỗi, không bị đổi thành không có dữ liệu.
- Semantic cache đổi contract sang v9, chỉ lưu khi có citation/evidence snapshot; lúc đọc lại kiểm tài liệu, version và quyền hiện tại. Các câu hỏi lịch sử, bản cũ, archive, tất cả/toàn bộ/đầy đủ/comprehensive bị loại khỏi semantic lookup và store trong phiên bản đầu.

**Kiểm tra GD5:** 236 ca focused đạt cho claim verifier, buffer/SSE, cache, history, feedback, policy, resilience, orchestration và evidence identity; thêm 61 ca contract cache đạt (32 ca ngoài phạm vi bị lọc bằng `-k`). `compileall` và `git diff --check` đạt. Chưa chạy provider/Qdrant/DB runtime, browser E2E, full-suite, recall hoặc p95; candidate chưa commit/deploy. GD6 chưa được triển khai: không thêm `search_mode`, cursor, quét lô hay worker tiếp tục.

**Đối chiếu ngày 2026-11-02:** các điểm D09/D10/D21 đã được chuyển từ guard lexical sang kiểm draft sau generation. Verifier chỉ nhận dữ liệu nguồn như dữ liệu không tin cậy, yêu cầu quote literal và kiểm nội dung từng ý; source group giữ SourceID riêng kể cả khi cùng tài liệu có nhiều đoạn. Khi verifier không hoạt động, hệ thống không phát draft và không gắn nhãn đã kiểm. Các giới hạn runtime/recall/p95 và nghiệm thu nghiệp vụ vẫn thuộc GD8/B1/B2; GD6 không nằm trong đợt này.

## GD6 — Hoàn tất candidate; chưa nghiệm thu runtime (2026-11-03)

Các đoạn dưới ghi lịch sử RED/GREEN; kết luận mới nhất ở mục chốt candidate cuối phần này.

Phạm vi chạm: `ChatMessageRequest` → `ChatTurnCommand` → HTTP adapter → `ChatRequest` → `RagRequest`/pipeline; kho SQL snapshot/cursor và danh mục nguồn; diagnostics/SSE, cache và quyền đọc lại. Fixture liên quan: GD6 contract/store/execution, chat runner/runtime adapter và API endpoints. Các thay đổi GD2–GD5 có sẵn trong working tree được giữ nguyên; baseline GD1 không chạy lại hoặc sửa.

RED/GREEN cục bộ: contract mở rộng có 16 ca đỏ (câu rộng bị mã cũ chặn, dấu đ/khoảng trắng, giá trị lạ/cursor rỗng), sau sửa 24 ca đạt. Ca truyền cursor trước generation đỏ rồi xanh; nhóm `test_gd6_search_contract.py`, `test_chat_turn_runner.py`, `test_chat_runtime_adapters.py` đạt 61 ca. Lệnh: `chat_env/Scripts/python.exe -m pytest -q backend/tests/unit/test_gd6_search_contract.py backend/tests/unit/test_chat_turn_runner.py backend/tests/unit/test_chat_runtime_adapters.py --tb=short`.

Chưa ghi nhận hoàn tất GD6: đang nối và review SQL/executor, cần kiểm tích hợp API và bảo mật cùng snapshot/cursor. Không chạy full suite, benchmark, recall production, browser E2E hoặc migration runtime. Không deploy, thay timeout/model/vector DB hay thêm worker; GD7/GD8 chưa triển khai.

### GD6 — Kiểm tra bổ sung 2026-11-03

Đã nối comprehensive vào pipeline với danh mục SQL chốt theo quyền và danh tính nguồn; migration V0050 chỉ bổ sung bảng scan/source, không backfill hoặc chạy trên runtime. Lưu CAS revision cùng trạng thái tài liệu, vị trí trang/ký tự, kết quả đã kiểm. Câu hỏi rộng bỏ qua cache; typed action GD2–GD5 giữ đường cũ, kết hợp comprehensive không tương thích bị từ chối ở boundary. Trang dài quét cửa sổ liên tiếp tối đa 8.000 ký tự, tối đa 3 nguồn/lượt; không bỏ phần còn lại rồi đánh dấu checked. Cursor cũ trả từng revision đã commit để không bỏ mất lô trung gian. Danh tính đoạn giữ doc/version/page/snapshot/hash và vị trí ký tự.

SSE service trả `search_progress` ở done/error; browser dùng thinking/done hiện có. Tiến độ phân biệt `turn_complete`, `snapshot_complete`, pending/error, stale/denied, cursor và `result_revision/results_pending`. Kiểm lại quyền trước lazy batch và trước đọc kết quả; stale/denied loại bỏ tên/số lượng nguồn trong phản hồi tiếp theo. Không thêm UI hoặc background worker.

Lệnh focused cuối: `chat_env/Scripts/python.exe -m pytest -q backend/tests/unit/test_gd6_api.py backend/tests/unit/test_gd6_search_contract.py backend/tests/unit/test_gd6_comprehensive.py backend/tests/unit/test_gd6_execution.py backend/tests/unit/test_chat_turn_runner.py backend/tests/unit/test_chat_runtime_adapters.py --tb=short` — **87 passed**. API test chạy HTTP/SSE với executor thật và pipeline fixture; store test chạy transaction SQLite; chưa phải SQL Server/provider runtime. RED trước sửa: final progress mất qua SSE (2 fail); trang dài thành error (1 fail). `git diff --check` và compileall các module mới đạt.

Ở lượt trước, pytest-cov lỗi collection ở import native NumPy/ONNX, rồi bcrypt/PyO3. Lượt cuối preload cả ba thư viện đã đo được coverage, không sửa dependency. Review độc lập đã nêu và sửa OCR bị bỏ, locator text bị hiểu thành structured, final SSE mất progress, replay bỏ revision; reviewer/sub-agent dừng do usage limit nên không có verdict độc lập cuối.

### GD6 — Chốt candidate

Đã đóng các mục còn lại: `db/scan_connection.py` áp dụng checkpoint/rollback và query/lock timeout theo ngân sách còn lại cho catalog, trang và transaction tiến độ. Không tăng timeout/retry budget. V0050 thêm bảng `RagComprehensiveBatch`; SQL chỉ đọc kết quả đúng một revision, không nạp toàn scan. Concurrent replay trả revision kế tiếp của cursor đầu vào, kể cả request thua CAS. Verifier GD5 thêm cờ bắt buộc coverage riêng cho comprehensive, không thay mặc định cũ; draft rỗng hoặc thiếu coverage không được tính checked.

Đã đối chiếu GD6.1–GD6.5: quick/input/câu rộng; catalog current/published/approved còn hiệu lực và có quyền; snapshot gắn doc/version/hash/trang cùng scope/query; từng cửa sổ nguồn giữ identity; lỗi/chưa đọc không complete; cursor owner/query/scope/mode/snapshot/revision; kiểm quyền trước lô, đọc và lưu; stale không nối nguồn mới; tiến độ phân biệt lượt truyền và snapshot. Dừng bằng hủy request, tiếp tục bằng cursor đã nhận, `result_revision/results_pending` hỗ trợ ghép không trùng. Nguồn error không tự retry vô hạn; bắt đầu scan mới sau khi xử lý nguyên nhân. Không backfill suy đoán.

**Kiểm tra cuối: 146 passed, 0 failed/skip. Coverage dòng sáu module GD6 mới: 446/477 = 93,50%.** Đây không phải coverage toàn repo hoặc mọi dòng GD2–GD5. Có API HTTP/SSE qua executor thật; scan → generation/verifier thật với provider fixture → SQL commit/resume; transaction/CAS/replay SQLite; thu hồi quyền/sai cursor/stale; timeout SQL fixture; hồi quy chat adapter/runner và SQL–HTTP history identity. Final review do Codex đối chiếu code/caller trực tiếp; không ghi nhận verdict độc lập chưa có.

Lệnh focused cuối (preload native để coverage không import lại extension):

```powershell
@'
import numpy, onnxruntime, bcrypt
import pytest
tests = ['gd6_search_contract', 'gd6_comprehensive', 'gd6_execution', 'gd6_sql_deadline', 'gd6_verified_scan', 'gd6_api', 'claim_verification', 'strict_stream_guard', 'chat_turn_runner', 'chat_runtime_adapters', 'chat_history_snapshot']
modules = ['rag.comprehensive', 'rag.comprehensive_execution', 'rag.search_mode', 'api.search_contract', 'db.repositories.comprehensive', 'db.scan_connection']
raise SystemExit(pytest.main(['-q', '-o', 'addopts=', *['backend/tests/unit/test_' + t + '.py' for t in tests], *['--cov=mech_chatbot.' + m for m in modules], '--cov-report=term-missing', '--cov-report=json:.local/gd6-coverage.json', '--tb=short']))
'@ | chat_env/Scripts/python.exe -
```

`git diff --check` đạt; cảnh báo hiện có là CRLF/deprecation. Chưa chạy migration/locking/deadline trên SQL Server thật, provider/Qdrant thật, mạng ngắt thực tế hoặc catalog lớn; fixture không chứng minh latency và chất lượng model. Kiểm lại catalog cần đọc fingerprint toàn phạm vi và vẫn phải dừng nếu hết ngân sách hiện có. Không chạy full suite, benchmark, recall production hoặc browser E2E. Không đổi dependency/config/runtime hoặc xóa dữ liệu.

GD7/GD8 chưa triển khai; không thay runtime, deploy, model, vector database, timeout/retry budget, reindex hoặc sửa baseline GD1.

## GD7 — Hoàn tất candidate; chưa nghiệm thu runtime (2026-11-03)

Đã đọc contract và trạng thái working tree trước sửa; giữ các thay đổi GD2–GD6 chưa commit. Phạm vi thực tế: `frontend/src/views/ChatView.vue`, `types.ts`, `api/client.ts`, `utils/chatScan.ts`, component `AnswerSources.vue` dùng lại `StructuredSourceFacts` và CSS nguồn hiện có. Bộ phân tích SSE hiện có được tái sử dụng; callback client nhận thêm `search_progress`, metadata kiểm chứng và `AbortSignal`. Không thêm dependency, viewer, store cursor, queue hoặc worker.

Backend chỉ nối dữ liệu tại các seam: `application/chat_turn.py`, `application/chat_result_history.py`, `adapters/chat_runtime.py`, `api/routers/chat.py`, `api/rag_server.py`, `rag/pipeline_steps.py`, `rag/comprehensive_execution.py`. Các trường optional `verification_status`, `answer_outcome`, `source_groups` được truyền tới UI và qua payload lịch sử hiện có. Nhóm nguồn chứa các identity citation tương ứng; group GD5 còn ở mức trang thì UI nói rõ trích đoạn thuộc nhóm vị trí, giữ các card đoạn riêng. Quick gửi đánh giá cuối sau generation; comprehensive lưu đánh giá cùng kết quả lô để replay giữ đúng trạng thái. Không lưu cursor vào lịch sử và không mở rộng quyền đọc.

UI giữ quick mặc định, dùng chế độ thực tế backend trả; “đã kết thúc lượt trả lời” riêng với hoàn tất snapshot. `results_pending=true` có thao tác “Xem tiếp kết quả đã lưu”. Dừng hủy request và giữ điểm đã nhận; thinking không được nhảy cursor hoặc xác nhận hoàn tất thay done. Kết quả chỉ ghép cùng scan và revision kế tiếp; replay không thêm lại nội dung/citation, khác phiên bản/đoạn vẫn còn. Đổi câu hỏi vô hiệu hóa thao tác tiếp tục cũ; đổi actor/unmount hủy request. Tiếp tục giữ payload/câu hỏi ban đầu, chỉ dùng lại sau reauthorization backend. Stale/denied xóa nội dung và nguồn cũ. Lịch sử cũ thiếu GD6 vẫn render; không có điểm tiếp tục sau reload thì gửi câu hỏi mới, không tái dựng cursor.

Review độc lập `gd7_review` (GPT-6.1-Sol, effort cao) đã tìm nhánh từ chối quyền ngay bước khởi tạo resume chưa xuất `denied`; đã sửa tại setup seam và xác nhận RED 3 ca rồi GREEN 5 ca liên quan. Review cũng phát hiện retry quick giữ lượt lỗi và nhãn kiểm chứng lô sau ghi đè lô trước; đã sửa chung và thêm/giữ hồi quy. Backend runner có RED trước nối metadata; frontend có RED cho kết quả thiếu trạng thái kiểm chứng không được nâng thành verified bởi revision sau.

### Kiểm tra tập trung GD7

Chạy từ `frontend`:

```powershell
node node_modules/vitest/vitest.mjs run src/__tests__/chat-scan.test.ts src/__tests__/chat-stream-gd7.test.ts src/__tests__/answer-sources.test.ts src/__tests__/chat-dashboard-login.test.ts src/__tests__/api-client-extended.test.ts src/__tests__/sse.test.ts
node node_modules/vue-tsc/bin/vue-tsc.js --noEmit -p tsconfig.json
node node_modules/vite/bin/vite.js build --outDir ../.local/gd7-ui-dist
$env:E2E_BASE_URL='http://127.0.0.1:5187'
node node_modules/@playwright/test/cli.js test tests/e2e/gd7-chat.spec.ts
```

**113/113 test frontend đạt; typecheck và build đạt; 2/2 ca Chromium fixture đạt.** Preview cô lập dùng output `.local/gd7-ui-dist`; mọi API trong hai ca browser được chặn bằng fixture. Luồng đã kiểm: comprehensive → tiến độ → dừng bằng bàn phím → focus về câu hỏi → tiếp tục/replay không trùng → revision tiếp theo → snapshot đã rà xong nhưng còn kết quả chưa nhận → nhận nốt → tải đúng URL ghim bằng bàn phím. Ca còn lại kiểm backend tự chuyển comprehensive và stale ẩn nguồn/nội dung, chặn tiếp tục, đưa câu hỏi về thao tác bắt đầu lại. Component kiểm thu hồi quyền 403, URL ngoài endpoint/review bị chặn, nhiều identity cùng trang và dữ liệu cũ. Đã xem ảnh fixture `.local/gd7-ui-fixture.png`; kiểm giao diện mới theo phạm vi hẹp, không audit accessibility toàn app. Detector UI không có finding.

Chạy từ repo:

```powershell
chat_env/Scripts/python.exe -m pytest -q -o addopts= backend/tests/unit/test_gd7_answer_assessment.py backend/tests/unit/test_gd6_execution.py backend/tests/unit/test_chat_turn_runner.py --no-cov --tb=short
```

**35/35 ca backend cuối đạt**: assessment/SSE/history reauthorization, runner, comprehensive/replay và từ chối resume ở setup. Các nhóm history/runtime/API bị ảnh hưởng đã được worker kiểm riêng (106 ca đạt); không cộng các nhóm trùng thành tổng mới. Tái sử dụng bằng chứng 146 ca GD6 và coverage 93,50% đã chốt ở trên; không chạy lại toàn bộ để làm mới báo cáo. `git diff --check` đạt.

Môi trường frontend tái sử dụng `node_modules` sẵn có của worktree `public-web-qa-fixes` qua junction, đối chiếu package.json giống nhau; không đổi package/lockfile hay cài lại môi trường. Output build nằm trong `.local`, không ghi đè thư mục đang phục vụ web. Preview thử nghiệm được dừng sau kiểm tra.

**Giới hạn:** chưa nghiệm thu SQL Server, migration V0050, provider/Qdrant, mạng ngắt thật, dữ liệu nghiệp vụ hoặc quyền runtime. Blob tải nguồn dùng bộ nhớ trình duyệt; chưa đo file lớn. Không kiểm toàn hệ thống, full suite, benchmark, recall hay p95. Không commit/push/deploy, chạy migration production, thay service/runtime, xóa dữ liệu, sửa baseline/oracle/lock GD1 hoặc triển khai GD8.


## GD8 — Chuẩn bị phát hành có giới hạn (2026-11-03)

### Lượt tiếp tục 03/11 — tracker chính tại candidate

**Đã sửa và xác nhận ba việc B02/B03/B05 trên một mẫu runtime thật có giới hạn; phát hành vẫn NO-GO.** Candidate `1eb82e0fef04496fbba1d56a79ca73bc466c602aafca8300816ce8c65c36b441` là **hash nội dung, không phải commit SHA**, nhánh `codex/gd8-acceptance-20261103`, nền `8118dc089dbd65eae2010e10883167f53b9d34e6` + WIP. Chưa commit/ký/push, chưa migration/cutover production.

| Việc | Phụ trách / phụ thuộc | Trạng thái | Bằng chứng / giới hạn |
|---|---|---|---|
| GD8.1 / A — candidate | Codex chính | Đóng băng1eb82; source/ZIP/assets đã đối chiếu | [Manifest](gd8-candidate-manifest.json); 1.102 source/ZIP và tám asset đã khớp; patch/index đã làm mới và kiểm hash sau lượt cuối. Gói chưa ký. |
| GD8.2 / B — runtime thật | Codex chính; nguồn/actor thật | Ba lỗi yêu cầu đã đóng trên mẫu bounded | B02/B05 verified/full_answer; B01/B03/B04 partial nhưng đủ nội dung được công bố. Comprehensive cùng scan phục hồi0/4→3/4→4/4; SQL chỉ revision1/2, không nhân bản. Giữ lần lỗi đầu và partial, không dùng receipt cũ thay bản hiện tại. |
| GD8.3 / C — B1/B2 | Codex chính | Ba ca mục tiêu đạt phạm vi đã công bố; B1 tổng thể chưa đạt | B02 PASS, B03 PASS_WITH_DISCLOSED_UNCERTAINTY, B05 PASS do đối chiếu trực tiếp nguồn/rubric. Ba nhóm nguồn bắt buộc vẫn thiếu; không công bố cải thiện chất lượng/hiệu năng toàn corpus. |
| GD8.4 / D — sửa lỗi/review | business_sources + Codex; reviewer độc lập | Focused và review đạt | Opaque-ID84/coverage module91,84%; generation31; stream27 ở caller không đổi. Các nhóm có thể trùng, không cộng. Sol-high không high/medium; token-service và cutover thực tế chưa kiểm. |
| GD8.5 / E — phát hành | Codex chính; B/C/D và duyệt riêng | NO-GO, chưa xin cutover | [Release request](../../.local/gd8-release/release-request.json) giữ blocker nguồn/vận hành; production8118dc0/V0048 không đổi. |

**Nghiệm thu hiện tại:** giữ nguyên rubric SHA256 `2ac0cd6b1b3b632ddf2b0c4ec69f6190c0c78ca3857c52606585ab69c8bba5ee`. Đây là development đã quan sát, root chấm trực tiếp theo nguồn, không lấy model tự chấm hoặc sửa oracle/holdout.

| Ca | Receipt trong `.local/gd8-release` | Kết quả bản1eb82 |
|---|---|---|
| B02 | `runtime-GD8-B02-opaque-transport-recheck.json` | **PASS**, done verified/full_answer: kế toán tách khoản tranh chấp/thiếu căn cứ và yêu cầu bổ sung; kho đối chiếu tồn/sổ, tách chênh lệch xác minh trước điều chỉnh. Giữ tính đề xuất và nguồn riêng, không bịa số/thời hạn. Lần đầu `runtime-GD8-B02-opaque.json` lỗi ReadTimeout Qdrant dense_query trước verifier được giữ nguyên; một lần kiểm lại transport thành công không chứng minh SLA. |
| B03 | `runtime-ui-b03-opaque.json` và PNG | **PASS_WITH_DISCLOSED_UNCERTAINTY**: đủ hai hành động literal, tách phạm vi kế toán/nhân sự và công khai quan hệ mâu thuẫn/thay thế chưa được xác nhận trong lần kiểm. Status vẫn partial/partial_answer đúng thực tế. Login, tải đúng SHA hai nguồn, history cùng câu trả lời, logout200→401, console không lỗi. |
| B05 | `runtime-GD8-B05-opaque.json` | **PASS**, done verified/full_answer: chu kỳ đánh giá/CAPA chưa xác nhận; ghi rõ thông tin thiếu, chuyển đầu mối xác nhận, giữ việc chưa hoàn tất và không tự điền giá trị. |
| B01/B04 | `runtime-GD8-B01-opaque.json`, `runtime-GD8-B04-opaque.json` | Nội dung yêu cầu được quan sát ở đúng1eb82, cả hai partial: B01 có yêu cầu bổ sung trong literal coverage; B04 đủ unknown và không suy triển khai thực tế từ duyệt nội dung. Không nâng thành verified. |

**Nguyên nhân và sửa chung:** prompt tách từng bước có nguồn thành claim nguyên tử, giữ tính đề xuất/điều kiện/phủ định; tách phạm vi khẳng định được khỏi bất định quan hệ. Verifier không gõ lại quote: copy opaque ID20hex SHA256 ràng buộc SourceID/passage/dòng/raw text. Resolver tra đúng bảng cục bộ, fail-closed khi collision, khác nguồn/passage, endpoint đảo hoặc không tồn tại; giữ nguyên `keepends` và giới hạn tổng mở rộng90.000 ký tự. Coverage giữ riêng các dữ kiện liên quan câu hỏi ngay cả khi draft bị loại; không đưa claim sai trở lại. Guard số/mã/phủ định/quyền, trạng thái backend và hợp đồng kết quả ra ngoài được giữ. Mô tả bước đề xuất không được coi là bằng chứng công ty đang thực hiện.

Receipt `verifier-opaque-id-contract-focused.json`84GREEN/91,84% chỉ module verifier; generation31 do root kiểm, caller stream27 không đổi được tái dùng có giới hạn. Review độc lập Sol-high không high/medium. Không cộng các nhóm kiểm trùng hoặc suy coverage toàn repo.

**Lịch sử giữ nguyên:** lượt10a bị chặn được lưu tại `historical_passes` sau khi người dùng yêu cầu sửa tiếp. Các bản selector số/line-ID dự đoán được và487/c17 từng còn thiếu action/quan hệ đều là intermediate, không chọn riêng kết quả xanh để hợp thức hóa bản cuối. B02 timeout cuối cũng giữ và công bố; không đổi timeout/retry budget/model/vector để vượt kiểm. Stop/Resume/replay/stale trước đây tiếp tục là bằng chứng implementation của executor/store/UI không đổi; prompt mới cần kết quả runtime riêng, scan complete không đồng nghĩa trả lời đủ mọi ý.

**Comprehensive mới được giữ trung thực:** lần đầu `runtime-GD8-B02-opaque-comprehensive.json` lỗi kỹ thuật do provider thêm claimID10 ngoài input1–9; parser từ chối đúng, pending0/4. Một lần resume cùng scan `kLIKLWzDs09nsUqWO4P8FK2ErdSHyLSt` đã lên3/4/revision1, results_pending=false và snapshot_complete=false. Lô bình thường cho Warehouse cuối đã đạt4/4 checked,0pending/0errors, snapshot_complete=true, revision2 và results_pending=false. `runtime-opaque-comprehensive-sql.json` xác nhận bốn DocID1173/1174/1176/1185 và chỉ batchrevision[1,2], không nhân bản từ lần lỗi đầu. Revision1 giữ ý kế toán, revision2 giữ đúng hành động kho; các phần partial vẫn ghi rõ. Đây là nghiệm thu kỹ thuật snapshot bốn nguồn, không phải nghiệp vụ toàn corpus. Không bỏ ID thừa hoặc nới parser để làm xanh.

**Còn lại cho phát hành:** ba nhóm B1 chưa có nguồn có thẩm quyền — phiên bản/thay thế nghiệp vụ thật, mâu thuẫn cùng đối tượng/điều kiện, trạng thái thực thi thật có phạm vi/thời gian. Tiếp theo là token-service/source RX packaging đúng manifest, snapshot baseline và DB COPY_ONLY/CHECKSUM/VERIFYONLY mới trong cửa sổ được duyệt, rollback đồng bộ source/UI/dependency/config/bundle và giữ schema/ledger/data cùng history bảo trì khi cần. Các bước này chưa thực hiện; chưa xin duyệt triển khai. Production kiểm mới14:00:55+07: ba service Running, local/public200 dbok/workerready, RAG8118dc0/live_authorized, V0048/noGD6tables, backend không diff. Đã dừng đúng bốn process test24348/24728/19564/13436,0remain; giữ DB test/artifact và ba service production vẫn Running. Root hoàn tất patch/index sau các cập nhật tài liệu; không có source hoặc production change.

### Bằng chứng bàn giao trước lượt tiếp tục

**Candidate được kiểm:** `dd202d2af9efdf00dedf4f54a987d1bb60a514d244d32733b20e70f4ce49d2a8`, nền Git `8118dc089dbd65eae2010e10883167f53b9d34e6` cộng WIP GD2–GD8. Kiểm tra chạy ở root trước phục hồi; đã đối chiếu đủ 1.101 file byte/hash khi giữ candidate tại `C:\Users\bao.nguyen\.codex\worktrees\gd8-candidate-20261103\ChatBotProject`. Các lần sửa/kiểm candidate tiếp theo phải dùng checkout này. Root production không còn chạy backend GD8.

- [Manifest candidate](gd8-candidate-manifest.json): hash từng file source, patch tracked, source ZIP và 8 asset frontend; chưa phải bundle được ký. Source ZIP: `.local/gd8-release/candidate-source.zip`; frontend mới: `.local/gd8-release/frontend-dist`. Không ghi đè `frontend/dist`.
- [Bằng chứng SQL, kiểm tra và khôi phục web](gd8-runtime-evidence.json): phân biệt DB test, candidate và baseline production.
- [Rubric nghiệp vụ đóng băng](gd8-acceptance.json): SHA256 `2ac0cd6b1b3b632ddf2b0c4ec69f6190c0c78ca3857c52606585ab69c8bba5ee`; không sửa oracle/lock/baseline GD1.
- Checkout không có AGENTS.md trên filesystem; áp dụng nội dung AGENTS người dùng cung cấp. `node_modules` junction còn hợp lệ, resolve cuối tới worktree `repo-restructure`; không cài dependency. Không commit/push hoặc cấp chữ ký mới.

### GD8.1 — Ma trận kiểm tối thiểu

| Yêu cầu | Bằng chứng tái sử dụng | Phần thiếu | Kiểm tối thiểu / kết quả GD8 |
|---|---|---|---|
| Danh tính nguồn, quick, lịch sử cũ | GD2 261 focused; GD5 và GD7 contract | Nối comprehensive vào persistence thật; link cùng version thay bytes | Đã RED/GREEN pin/history và pin nguồn; xem GD8.4 |
| Migration và SQL scan/resume | GD6 146 ca, coverage 93,50%; store trước dùng SQLite | SQL Server transaction/CAS/locking, schema thực tế | Production chỉ V0048; DB test mới áp49→50 hai lần, 6 ca SQL thật đạt |
| Snapshot đủ phạm vi được đọc | Catalog/ACL GD6 | Nguồn CHUNG có trong canonical ACL nhưng thiếu catalog | Sửa helper SQL dùng chung; test so với protected_files, giữ site/security/lifecycle |
| Dừng/tiếp tục/replay, revision, results_pending | GD7 113 frontend + 35 backend, 2 Chromium fixture | Mạng/provider/UI thật và luồng đầy đủ candidate | Tái dùng fixture; SQL replay/CAS mới đạt; không gọi đây là runtime E2E |
| Thu hồi/thay nguồn giữa lượt | GD6/GD7 stale/denied tests | SQL và persistence/link pin cùng bản candidate | SQL stale/deny callback, history mutation, HTTP nguồn đổi bytes được kiểm; ACL runtime thật còn thiếu |
| Nội dung có nguồn chuẩn | 13 nguồn duyệt và oracle39 lịch sử | Hỏi rộng, nhiều nguồn/phạm vi; nguồn nhiều version/mâu thuẫn thật | Đã chốt5 ca trước runtime theo ủy quyền; các nhóm không có nguồn vẫn chưa đủ |
| Retrieval/provider → verifier → SSE/UI → mở nguồn → history | GD7 API/browser fixture; không có chứng cứ runtime candidate | Candidate đúng định danh và bundle/môi trường test được phép | Chưa chạy; health bản phục hồi8118dc0 không thay thế được |
| B2 recall/p95/chi phí | Chỉ có GD1 diagnostic với nguồn/draft cấp sẵn | Baseline thật cùng nguồn/quyền/model và tiêu chí được chốt | Không đặt SLA, không benchmark hoặc suy chất lượng từ coverage |

### GD8.2 — SQL thật, phạm vi và lệnh

DB mới `Mech_Chatbot_Test_GD8_20261103_01a19f1f` được tạo riêng, giữ lại để xem xét. Bootstrap bằng baseline + seed role/phòng ban và migration có sẵn tới V0048; không nạp tài khoản dev, không xóa hoặc dùng lại DB cũ. Dùng `split_batches`, `strip_use_statements`, `apply_migration` của runner hiện có, bind engine đích rõ ràng và assert `DB_NAME()` trước ghi. V0049 và V0050 chạy hai lần đạt, đủ ba bảng scan/source/batch, không có grant `document_history_reader`.

Sáu ca SQL Server thật kiểm revision1→2, replay không ghi trùng, hai writer cùng revision chỉ commit một, stale có lưu trạng thái, từ chối kết quả khi callback thu hồi quyền, rollback lô sai và timeout khi hàng bị khóa. Catalog/actor vẫn là dữ liệu kiểm soát, không chứng minh retrieval/provider hay ACL production. Không gọi fixture là nghiệm thu runtime đầy đủ.

Lệnh chốt chạy trước khi chuyển candidate (38 passed, 0 failed):

```powershell
# Các lần chạy sau: cwd phải là checkout candidate ở trên;
# dùng interpreter chat_env của root, không chạy test GD8 với backend production.
$env:RUN_DB_TESTS='1'
$env:GD8_SQL_DATABASE='Mech_Chatbot_Test_GD8_20261103_01a19f1f'
& C:/Users/bao.nguyen/Documents/ChatBotProject/chat_env/Scripts/python.exe -m pytest -q -o addopts= backend/tests/unit/test_gd8_comprehensive_history.py backend/tests/unit/test_gd8_shared_scope.py backend/tests/unit/test_gd8_regular_source_pin.py backend/tests/integration/test_gd8_sql_runtime.py backend/tests/unit/test_gd6_sql_deadline.py --no-cov --tb=short
```

Receipt gốc `.local/gd8-final-focused.xml` tại root. Build mới bằng `node node_modules/vite/bin/vite.js build --outDir ../.local/gd8-release/frontend-dist` đạt; `git diff --check` đạt. Không chạy lại full GD2–GD7, browser toàn hệ thống hoặc đo coverage toàn repo. Coverage GD6 93,50% là bằng chứng lịch sử, không gán cho mọi dòng sửa GD8.

### GD8.3 — Nguồn và đáp án

Chủ sở hữu đã trả lời **“bạn có thẩm quyền”** ngày03/11, giao Codex xác nhận đáp án bằng đối chiếu nguồn. Đã xác minh hash13 Markdown khớp `docs/business-drafts-20261027/approval.json`, oracle39 khớp hash trong `business-source-review.json`. Chốt5 ca: hỏi thanh toán không tên file; nhiều đoạn kế toán/kho; khác phạm vi kế toán/nhân sự; dữ kiện/trạng thái chưa xác nhận; thiếu căn cứ và phân biệt lỗi kỹ thuật. Không dùng chatbot tự chấm hoặc biến approval nguồn thành kết quả runtime.

B1 đã đóng khoảng trống thẩm quyền, còn thiếu nguồn xác nhận nhiều phiên bản/thay thế, mâu thuẫn cùng điều kiện và trạng thái hoạt động thật. Rubric mới là development acceptance đã quan sát nguồn, không phải holdout chưa dùng. Chưa đọc/chạy/sửa holdout GD1 và chưa dùng đáp án runtime mới để chỉnh rubric. **Chưa đạt nghiệm thu nghiệp vụ.**

### GD8.4 — Bốn blocker đã sửa

| Blocker / RED | Sửa tại candidate | GREEN và giới hạn |
|---|---|---|
| Comprehensive trả lời được nhưng `capture_sources` từ chối lưu vì thiếu evidence_pin; 6 RED | `rag/comprehensive_execution.py`: tạo pin từ đúng native/OCR/vision + context trong transaction đã kiểm quyền, trước generation | 20 ca history/execution; JSON replay, nhiều chunk, đổi text/context/version/ACL làm history bị chặn; SQLite |
| Catalog bỏ nguồn CHUNG rồi báo complete; 3 RED | `db/repositories/ui_queries.py`: dùng cùng CHUNG implicit grant như protected_files, giữ site/security/serving | 69 ca helper/caller/deadline, thêm2 catalog riêng; không cộng tổng trùng |
| URL thường chỉ pin version; cùng version đổi bytes vẫn trả200; 8 RED | `application/chat_citations.py`, `api/source_file_pins.py`, routers chat/documents, repository source_page_images: giữ snapshot/hash, ACL trước identity, kiểm bytes khi đọc; ẩn preview chưa có hash | 174 ca pin/routes/history/identity; nguồn sai pin trả404; HTTP fixture, không production |
| SQL timeout làm connection invalidated; khôi phục driver.timeout phát lỗi closed connection che HYT00; 1 RED trên SQL thật | `db/scan_connection.py`: chỉ phục hồi timeout khi connection còn hợp lệ | 6 SQL thật +2 deadline unit đạt; không tăng budget/retry |

Review độc lập GPT-6.1-Sol effort cao đã xem quyền/citation/history, completeness/resume và rollback; không còn finding ưu tiên cao trong bốn patch. Nhóm38 cuối kiểm chung các thay đổi; không cộng 20/69/174 hoặc GD6/GD7 thành tổng mới.

### GD8.5 — Gói phát hành và điều kiện chuyển

Quy trình dùng lại: [Windows service](../../docs/windows-service-operations.md), [release/rollback](../../docs/audit-confirmed-fixes-release-plan-20261030.md), [migration](../../database/MIGRATIONS.md). Không áp SHA/migration lịch sử của các runbook như dữ liệu hiện tại.

1. Giữ candidate theo manifest, chốt kết quả runtime/nghiệp vụ còn thiếu. Khi được duyệt riêng, commit candidate sạch và ràng buộc bundle v8 mới đúng SHA; không dùng bundle8118dc0 để cấp quyền cho WIP.
2. Trước cutover phải có snapshot **mới** của baseline thực tế: source/frontend/dependency/service config/ACL/bundle; secret ngoài Git với quyền bảo vệ. Backup DB theo quy trình có receipt `COPY_ONLY/CHECKSUM` và `VERIFYONLY`, quyền restore và cửa sổ dừng sạch. Không dựa vào rollback tree cũ đã bỏ.
3. Đối chiếu DB đích/pending schema; principal được phép áp V0049 trước V0050. Không cấp role lịch sử tự động, không backfill suy đoán. Không dùng service principal để nới quyền migration.
4. Chuyển đúng source/UI/dependency/bundle sau khi dừng sạch, chạy preflight identity hiện có. Kiểm quyền đọc của ba service account trên **file source mới** trước start; WIP tạo mới đã gây lỗi PermissionError thực tế.
5. Hậu triển khai tối thiểu: health/worker; login và quyền; một câu SSE có nguồn; comprehensive→dừng→resume/replay→revision liên tiếp, snapshot_complete/results_pending; mở đúng nguồn; history; logout. Service Running hoặc bộ fixture không đủ.
6. Nếu thất bại, quay lui source/UI/dependency/config/bundle cùng baseline đã chụp, **giữ V0049/V0050, ledger và mọi scan/source/batch đã ghi**, không DROP/down-migration/xóa dữ liệu. Chưa diễn tập tương thích đọc/ghi rollback trên runtime đầy đủ; kiểm hẹp trước cutover. Các cursor GD8 không tiếp tục được trên baseline cũ; bắt đầu phiên mới khi dùng lại chức năng.

**Quyết định:** hoàn tất gói chuẩn bị để xem xét; chưa đạt nghiệm thu runtime/nghiệp vụ; chưa đủ điều kiện triển khai GD8. Cần phê duyệt riêng commit/ký bundle phù hợp để dựng candidate test đúng định danh; bổ sung nguồn cho các nhóm B1 còn thiếu. Phê duyệt khôi phục web bên dưới không phải phê duyệt phát hành GD8.

### Khôi phục web được duyệt riêng trong GD8

Ngay đầu khảo sát, App/RAG/Worker đều Stopped. Người dùng thử Start rồi xác nhận chúng tự Stop; log ghi không đọc được source mới `domain/evidence_identity.py`. Qdrant thực tế là **Cloud**, health200; kiểm cổng6333 local ban đầu không phản ánh cấu hình vận hành.

Người dùng duyệt **“Duyệt phục hồi bản đã ký, giữ GD8 riêng”**. Đã tạo checkout candidate riêng, đối chiếu1.101 hash, giữ source ZIP/patch; phục hồi48 file backend tracked về8118dc0 và chuyển8 module mới ra ngoài cây import, giữ tại `.local/gd8-release/parked-untracked-backend` cùng bản candidate. Git ghi lại file nên DACL cũ được phục hồi từ snapshot; không thay ACL dữ liệu nghiệp vụ. Frontend đang phục vụ, DB/config/bundle được giữ.

Phiên Codex không có quyền SCM Start; người dùng đã Start cả ba qua Task Manager. Kiểm mới: cả ba Running, local/public health200 với `db=ok`, worker `ready`; RAG authenticated health200, `git_sha=8118dc0`, `rag_loaded=true`, `live_authorized=true`, bundle `acb279732e13610e62de6590afa13b2bfc78e28f876ea551fc54c4cdbcc34f4e`. Trang local/public200, 14 lượt asset khớp byte với `frontend/dist`. Backend root `git diff HEAD -- backend/src` rỗng. Không chạy migration production, không ký bundle mới; **production được khôi phục về bản cũ, GD8 chưa triển khai**.

Chưa kiểm trong GD8: provider/SSE/UI/citation/history xuyên suốt trên candidate, mạng ngắt thật, nguồn nhiều version/mâu thuẫn thật, nghiệm thu nghiệp vụ, full suite/load/benchmark/recall/p95, restore DB/vector hoặc browser E2E production. Không suy các mục này từ kết quả khôi phục health/assets.

### Bàn giao sang Claude — 2026-11-03

Tracker chính từ đây là bản này trong nhánh `codex/gd8-acceptance-20261103` (commit đầu tiên của candidate: `6dbe1dc`, đã push lên GitHub). `origin/main` đã được fast-forward lên `8118dc0` (bản đang chạy production); 9 nhánh remote đã merge đã xóa.

**Rà nguồn sẵn có cho ba nhóm B1 (chỉ đọc, `Mech_Chatbot_DB` + `data/raw` + thư mục bản vẽ ở root):**

| Nhóm B1 | Kết quả rà | Kết luận |
|---|---|---|
| Nhiều phiên bản / thay thế thật | 174 tài liệu; chỉ 3 bản `superseded`, đều là file QA/demo/eval tổng hợp. Sáu mã bản vẽ thật (9.1.00678, 9.3.03843, 9.3.03844, 9.3.03951, 9.3.04068, 9.3.04080) mỗi mã chỉ có một phiên bản | Vẫn thiếu; cần chủ tài liệu cung cấp ít nhất một tài liệu thật có hai phiên bản và quan hệ thay thế |
| Mâu thuẫn thật cùng đối tượng | Doc60 trang 1, vị trí C2, mã `8.3.06316.010`: cột vật liệu ghi Inox 201, mô tả ghi Inox 304 (đã đối chiếu hash tại `reports/rag-14-departments-20261005/primary-deployment-20261017/drawing-pending-evidence/README.md`) | Đóng được một phần: là mâu thuẫn trong một nguồn, chưa phải mâu thuẫn giữa hai nguồn. Đáp án kỳ vọng chỉ là nêu đủ hai giá trị và giữ chưa xác nhận; chưa thêm vào `gd8-acceptance.json` |
| Trạng thái áp dụng/triển khai thực tế | 13 tài liệu nghiệp vụ chỉ có ngày hiệu lực phát hành 28/10/2026; theo quyết định của plan không được suy trạng thái thực thi từ ngày phát hành | Vẫn thiếu; cần chủ nghiệp vụ xác nhận trạng thái thực tế có phạm vi và thời gian |

Không sửa code, không chạy runtime mới, không thay đổi production trong lượt này.

### GD8.5 — Đã phát hành 2026-11-03 (chưa nghiệm thu nghiệp vụ)

Quyết định của chủ sở hữu (bao.nguyen, 03/11): "Phát hành, ghi chưa nghiệm thu". Production chạy commit `85e527c028d2b82a0725f200111161ce02ddc962`, bundle v8 `36c55b508ea6493b4414dcc6d4247b16b82a00b45bd3205ff3e1faae6343857b` (grants/versions không đổi so với 8118dc0), schema V0050.

| Hạng mục | Kết quả |
|---|---|
| Backup | `COPY_ONLY, CHECKSUM` + `RESTORE VERIFYONLY` đạt trước migration (bản 89 MB, lượt 3); lượt 4 tạo thêm một backup mới |
| Migration | V0049/V0050 áp một lần; `Roles` 8 -> 9, role `document_history_reader` chưa cấp cho ai; 3 bảng scan rỗng; 59 bảng còn lại giữ số dòng |
| Code/UI | Checkout production detached tại `85e527c`; `frontend/dist` khớp từng byte bản build GD8 qua local và public |
| Quyền đọc source | Preflight dưới từng service identity: 3/3 `ok`, có `python_source_read` |
| Dữ liệu | 6.486 file vùng bảo vệ không đổi; `database/seed/01_roles.sql` đổi theo commit phát hành |
| Health | Local/public 200, `db=ok`, worker `ready`; RAG `git_sha=85e527c`, `live_authorized=true` |
| Lỗi sau khởi động | Không có lỗi trong log App/RAG/Worker sau lần khởi động mới |

Bốn lượt chạy (biên nhận giữ trong `.local/runs/gd8-deploy-20261103/attempt*`): lượt 1 dừng ở drain do vòng đồng bộ Qdrant định kỳ 10 phút của worker, không tắt service; lượt 2 sai đường dẫn file hỗ trợ, helper từ chối trước SQL; lượt 3 chạy backup/migration/cài đặt rồi vấp kiểm dữ liệu do file seed, tự rollback code về 8118dc0 và giữ schema; lượt 4 đạt.

**Chưa kiểm sau phát hành:** login, chat SSE có nguồn, comprehensive dừng/tiếp tục, mở nguồn, lịch sử, logout trên trình duyệt. Claude không đăng nhập production bằng mật khẩu; chủ sở hữu tự kiểm.

**Chưa nghiệm thu nghiệp vụ:** B1 nhóm nhiều phiên bản thật và nhóm trạng thái áp dụng thực tế không có nguồn; nhóm mâu thuẫn chỉ có ứng viên trong một nguồn (Doc60, `8.3.06316.010`). B2 baseline recall/p95/chi phí chưa đo.
