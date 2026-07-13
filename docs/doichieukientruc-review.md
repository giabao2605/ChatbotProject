# Review `doichieukientruc.docx` đối chiếu source hiện tại

Ngày review: 2026-07-13  
Nguồn chính: source code và `logs/rag_trace.jsonl` trong checkout hiện tại. Không quét `chat_env`, `chat-ui/node_modules`, `chat-ui/.next`.

## Kết luận

Hướng ưu tiên tổng thể của tài liệu là hợp lý: sửa cơ chế retry/repair và kiểm chứng claim trước, thử late interaction ở tầng rerank, chỉ decomposition khi query phức tạp, và để GraphRAG thành route riêng. Tuy nhiên tài liệu chưa đủ tin cậy để dùng làm căn cứ định lượng hoặc ticket triển khai nguyên trạng. Phần số liệu không ghi snapshot/phạm vi/cách đếm; một số mô tả đã lệch source hiện tại; và đề xuất BGE-M3 multi-vector chưa chứng minh được khả năng tích hợp với stack đang dùng.

## Findings

### High — Bảng số liệu refusal không có provenance và đã không khớp log hiện tại

Tài liệu nêu 1.126 lượt Evidence Gate (1.112 pass, 14 block), `post_check_numbers=174`, `no document=257`, `exact code=117`, `candidate selection=69`, nhưng không ghi file nguồn, mốc thời gian, commit, tiêu chí deduplicate hay câu lệnh tính. Các số này tái lập được nếu cắt `logs/rag_trace.jsonl` tại event Evidence Gate thứ 1.126 (`2026-07-13T01:07:36.721243Z`), ngoại trừ bảng đã bỏ 15 `client_cancelled`. Trên toàn bộ log tại thời điểm review (2026-07-09 đến 2026-07-13), parse JSON cho kết quả 2.450 event `evidence_gate` (2.436 true, 14 false) và 1.265 `rag_end` refusal; riêng `post_check_numbers=268`, `no_retrieved_docs=617`, `evidence_gate=14`. Log còn trộn trace test (`strict-stream-test`) và event `rag_end` có `refusal_reason=null`, nên không thể coi số đếm thô là production KPI.

Hệ quả: kết luận “Evidence Gate chỉ khoảng 2% tổng refusal” có thể đúng cho một snapshot ngầm định, nhưng không tái lập được và không đủ cơ sở để đặt P0 hay mục tiêu “giảm 50%”.

Action: bổ sung ngay vào DOCX: commit SHA, khoảng timestamp, môi trường, danh sách event/reason được tính, quy tắc loại test/cancel/null/duplicate, script hoặc SQL tái lập, denominator chính xác. Tách metrics production khỏi test/eval.

Nguồn: `logs/rag_trace.jsonl`; nơi phát event: `src/mech_chatbot/rag/pipeline.py:699-712`, `src/mech_chatbot/rag/pipeline_steps.py:502-515`.

### High — Mô tả Evidence Gate là nút Pass/Fail tĩnh không phản ánh cấu hình mặc định hiện tại

`verify_answerability()` chỉ block bằng heuristic ở đầu; LLM verifier mặc định tắt qua `LLM_EVIDENCE_VERIFIER_ENABLED=false`, và khi tắt hàm trả pass với reason `deterministic_evidence_gate_passed`. Nếu LLM verifier lỗi hoặc JSON lỗi, code cũng fail-open. Vì vậy 2.436 event `answerable=true` không đồng nghĩa 2.436 lần một evaluator đã đánh giá evidence là đủ.

Hệ quả: so sánh “Evidence Gate pass 1.112/1.126” và thiết kế CRAG dựa trên tỷ lệ pass đó dễ suy diễn sai về chất lượng evaluator. Vấn đề thực tế là thiếu một evaluator có score/state đáng tin cậy, không chỉ là threshold quá gắt.

Action: sửa tài liệu thành “heuristic gate + optional LLM verifier (default off) + deterministic post-check”; trước khi làm CRAG, định nghĩa telemetry phân biệt `heuristic_pass`, `heuristic_block`, `verifier_pass`, `verifier_block`, `verifier_fail_open`.

Nguồn: `src/mech_chatbot/rag/evidence_gate.py:118-181`.

### Medium — Nhận định `has_unsupported_numbers()` đúng hướng nhưng phạm vi bị nói quá rộng

Code đúng là so tập token số trong answer với context + question và bỏ qua 0–10. Tuy nhiên post-check chỉ chạy khi `is_high_risk_question(question)` trả true: caller truyền `strict_mode=is_high_risk_question(...)`, còn hàm trả false ngay nếu không strict và không high-risk. Comment tại caller cũng nói chủ ý không chặn policy answer thông thường chỉ vì format khác.

Hệ quả: các ví dụ false negative về format/đơn vị/phép tính là plausible từ implementation, nhưng tài liệu chưa cung cấp case tái hiện hoặc tỷ lệ false-negative đã gán nhãn. Không nên gọi đây là “nút thắt lớn nhất” chỉ từ số log.

Action: thêm test corpus tối thiểu cho `1,500 ↔ 1500`, quy đổi đơn vị, phần trăm suy ra, tổng BOM và số citation; đo precision/recall của post-check trước khi thay bằng claim verifier.

Nguồn: `src/mech_chatbot/rag/evidence_gate.py:184-214`; `src/mech_chatbot/rag/pipeline_steps.py:497-515`.

### Medium — Tuyên bố filter fail-closed bỏ sót ngoại lệ legacy admin

Với user thường, source đúng là compose department, clearance và site theo hướng fail-closed, rồi giữ các điều kiện đó ở broad fallback. Tuy nhiên role legacy `admin` trả `rbac_filter=None`, tức bypass các filter department/security/site ở retrieval. Vì vậy câu “filter fail-closed theo department, site, clearance” không đúng tuyệt đối.

Action: ghi rõ admin/audit exception và xác nhận đây là policy có chủ đích; bổ sung test leakage riêng cho admin nếu tài liệu dùng mục tiêu `RBAC/lifecycle leakage = 0`.

Nguồn: `src/mech_chatbot/rag/rbac.py:88-129`, `143-164`; `src/mech_chatbot/rag/retrieval.py:15-51`.

### Medium — Luồng kiến trúc tóm tắt thiếu điều kiện và có thể tạo ấn tượng mọi bước luôn chạy

Các phần Dense + BM25 + RRF, strict→broad, HyDE khi rỗng, SQL BOM, diversification, Voyage/local fallback và parent hydration đều có trong source. Nhưng chúng là các nhánh có điều kiện: HyDE chỉ chạy một lần khi retrieval rỗng và đủ điều kiện; Voyage chỉ khi policy chọn backend; SQL BOM cần part IDs và mechanical context; LLM evidence verifier mặc định không chạy.

Action: đổi sơ đồ tuyến tính thành sơ đồ có decision nodes và ghi default/feature flag. Điều này cần làm trước khi biến kiến trúc mục tiêu thành tickets.

Nguồn: `src/mech_chatbot/rag/pipeline.py:333-397`, `456-544`, `610-655`; `src/mech_chatbot/rag/pipeline_steps.py:666-714`; `src/mech_chatbot/rag/context_builders.py:403-452`.

### Medium — “Pilot BGE-M3 multi-vector trước vì đã dùng BGE-M3” chưa phải đường nâng cấp ít thay đổi đã được chứng minh

Project đang dùng `HuggingFaceEmbeddings` cho một dense vector và `FastEmbedSparse("Qdrant/bm25")` cho sparse; collection được tạo với một dense vector không tên và một sparse vector tên `sparse`. Không có BGE-M3 lexical sparse hoặc ColBERT-style multi-vector trong ingestion/query path. Việc cùng tên model không tự động làm multi-vector tương thích với wrapper, schema collection, ingestion/backfill và query API hiện tại.

Action: trước P1, làm spike riêng xác minh output API của BGE-M3, Qdrant multivector schema, LangChain/Qdrant client support, kích thước index, latency và migration path. So sánh ba baseline: hiện tại, Voyage rerank hiện tại, late-interaction rerank; không mặc định chọn BGE-M3 chỉ vì model dense đang trùng.

Nguồn: `src/mech_chatbot/rag/bootstrap.py:76-118`; `src/mech_chatbot/rag/rerank.py:72-164`.

### Low — Citation được mô tả nhiều trường hơn canonical identifier thực tế

Tài liệu nói citation dùng `DocID + PageNo + Version + SourceID`. Canonical SourceID thực tế có dạng `D<DocID>P<PageNo>`; version và filename là metadata/format citation đi kèm, không nằm trong SourceID. Đây không phải lỗi kiến trúc nhưng cần dùng thuật ngữ chính xác khi thiết kế claim verification.

Action: phân biệt `source_id`, `version_no`, `file_name` và rendered citation; verification phải bind version của evidence set, không suy ra version từ SourceID.

Nguồn: `src/mech_chatbot/rag/answer_checks.py:130-177`; `src/mech_chatbot/rag/prompt.py:68-70`; `src/mech_chatbot/api/app_server.py:499`.

## Những phần được source xác nhận

- Hybrid retrieval hiện tại thực sự chạy dense và BM25 độc lập rồi RRF; không chỉ cosine search.
- Filter current/published/approved/servable, lifecycle exclusions và RBAC/site/security được compose ở retrieval boundary.
- Có strict exact → broad fallback cho part/code query và HyDE fallback một lần khi rỗng.
- SQL BOM giữ DocID/PageNo để citation; candidate diversification, Voyage rerank với local fallback và parent-context hydration đều đã có.
- Chưa thấy implementation GraphRAG, late-interaction/multivector, query decomposition planner hay CRAG loop nhiều pass trong source hiện tại.

## Quyết định đề xuất cho tài liệu

Giữ thứ tự chiến lược `CRAG/repair → late interaction benchmark → conditional decomposition → governed GraphRAG`, nhưng hạ các tuyên bố định lượng thành giả thuyết cho đến khi có snapshot tái lập và labeled eval. P0 đầu tiên nên là chuẩn hóa telemetry/eval và tái hiện false-negative, sau đó mới sửa post-check hoặc thêm correction loop.
