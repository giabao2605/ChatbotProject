# Chuẩn bị owner review cho Grounded Math

## Trạng thái và ranh giới

Đây là tài liệu chuẩn bị offline cho campaign Grounded Math controlled-demo hiện
tại. Nó không phải review evidence, owner decision, activation bundle hoặc quyền
chạy traffic hay bật tính năng.

Chỉ dùng tài liệu này sau khi canonical campaign gate đã reconcile đủ frozen
manifest 100 card và trả về trạng thái cho phép bắt đầu review theo declaration
đã ký. Không sửa live window, operator script, threshold, runtime, corpus, WAL
hoặc evidence hiện có để làm một case trở thành eligible.

## Điều kiện trước khi khóa review set

Chỉ khóa review set khi tất cả điều kiện sau đạt:

- exact serving commit, runtime identity, activation scope, corpus fingerprint,
  manifest hash, owner declaration và operator-tool hash vẫn khớp current window;
- mọi planned card có đúng một terminal disposition; không có ambiguous,
  replacement, retry, catch-up hoặc extra dispatch;
- canonical base gate và companion operator gate đồng ý trên đúng cùng tập trace
  hash;
- Grounded Math release decision vẫn chưa được đặt và default rollout vẫn OFF;
- input cho sampling chỉ gồm metadata và trace hash. Không đưa raw prompt, answer,
  document text, operand, quantity, formula, credential hoặc private trace ID vào
  sampling artifact.

Nếu một điều kiện không đạt, dừng và giữ nguyên artifact để owner disposition.
Không sửa sample bằng cách xóa hoặc thay case.

## Contract của review set

Governance hiện hành là `single_owner`:

- `bao.nguyen` cung cấp đủ 20 primary human label;
- Codex chỉ được chuẩn bị metadata và kiểm tra cơ học, không phải independent
  human reviewer;
- mọi failure, unexpected access-denied và low-confidence case bắt buộc owner
  review;
- `tran.nghi` không phải reviewer primary thứ hai của sample 20 case này.
  Technical review cho interaction matrix về sau là gate riêng.

Tập bắt buộc review là hợp của:

1. mọi mandatory-risk case; và
2. đủ case đã chọn phân tầng trước để đạt 20 primary case.

Nếu mandatory-risk case vượt quá 20 thì review tất cả. Không bỏ risk case chỉ để
giữ tổng đúng 20.

## Cách khóa sample

Sample phải được khóa trước khi owner mở bất kỳ answer nào.

1. Lập bảng sampling từ canonical gate metadata. `trace_id_sha256` phải thuộc
   canonical reconciled trace set; `card_id` và `prompt_sha256` phải đồng thời
   khớp frozen public manifest. Mismatch, duplicate hoặc missing binding đều
   phải dừng.
2. Chỉ dùng automated metadata để đánh dấu mandatory-risk: failure, unexpected
   access denied hoặc low confidence.
3. Thêm toàn bộ mandatory-risk case trước.
4. Với mỗi case, định nghĩa `stratum` chính xác là tuple `(operation,
   operand_style, eligible_outcome, document_identity_sha256)`. Current
   inventory chỉ có một production-PDF document class, nên vẫn ghi nhận
   document class nhưng không thêm nó vào tuple cân bằng của window này.
5. Điền từng slot bằng candidate thuộc stratum đang có ít selected case nhất.
   Nếu nhiều stratum hòa, so sánh tuple theo thứ tự trường ở bước 4. Trong
   stratum được chọn, xếp tăng dần theo SHA-256 của canonical manifest hash nối
   với `trace_id_sha256`, rồi lấy case đầu chưa chọn. Quy tắc này cho kết quả
   deterministic mà không cần đọc nội dung answer.
6. Khi vẫn còn stratum khác, cùng một cặp document-operation không chiếm quá hai
   primary case.
7. Ghi một lần ordered trace-hash set và canonical SHA-256 của tập đó. Không sinh
   lại sample sau khi review bắt đầu.

Quy tắc tie-break deterministic ở trên mới là đề xuất cho review artifact. Phải
bind rõ trước khi sử dụng; tài liệu này không tự sửa campaign declaration đã ký.

## Rubric của owner

Với mỗi case đã chọn, owner xem qua authorized UI hoặc review surface đã được
duyệt và chỉ ghi metadata judgement.

| Field | Điều kiện pass |
|---|---|
| `calculation` | Dùng đúng document và operation, chọn đúng operand, cho đúng kết quả số. |
| `formula` | Công thức hiển thị hoặc được suy ra khớp operation và không thêm phép biến đổi ngoài phạm vi. |
| `unit` | Unit đúng và nhất quán, hoặc được ghi rõ là không áp dụng; không tự bịa unit. |
| `citation` | Answer cite đủ evidence cần thiết để hỗ trợ phép tính. |
| `provenance` | Source và document identity được cite đúng với nguồn đang serve và được authorize. |
| `correct` | Mọi correctness, safety và access-control check áp dụng cho case đều đạt. |
| `confidence_band` | Band khớp mức mạnh của evidence và refusal behavior quan sát được. |
| `reason_code` | Controlled reason code giải thích judgement fail hoặc not-applicable mà không chép raw content. |

Review artifact chỉ nên chứa reviewer identity, review time, card ID, trace hash,
các boolean trên, confidence band, reason code và những frozen binding hash.
Không lưu raw answer hoặc document content dưới dạng free text.

Các nhóm controlled reason code đề xuất:

- `pass`
- `wrong_operation_or_operand_scope`
- `wrong_formula_or_result`
- `unit_error`
- `citation_or_provenance_error`
- `unexpected_access_decision`
- `confidence_or_refusal_error`
- `insufficient_authorized_evidence`

Bất kỳ primary judgement nào fail đều giữ review gate fail-closed, chờ owner
disposition. Review hoàn tất không tự authorize default rollout.

## Kiểm tra cơ học trước owner sign-off

- sample và reviewed trace-hash set khớp chính xác;
- không có primary case trùng, thiếu hoặc thừa;
- mọi mandatory-risk case đều có mặt;
- mọi primary label đều do `bao.nguyen` thực hiện và review không bị mô tả là
  independent;
- giới hạn cùng document-operation được giữ khi còn stratum thay thế;
- artifact không chứa raw prompt, answer, document, operand, quantity, formula,
  credential hoặc private trace identifier chưa hash;
- campaign, gate, review-set, governance, runtime và source-commit hash đều được
  bind;
- default rollout authorization vẫn false.

## Chỉ chuẩn bị interaction matrix

Không chạy interaction matrix khi campaign 100 card hoặc owner review chưa hoàn
tất. Sau khi cả hai gate pass và final RC đã freeze, chuẩn bị matrix nhỏ nhất so
sánh `all_off` với Math-only ở concurrency 1 và 5. Query, CRAG, Claim Repair,
Community Summaries và Late Interaction nằm ngoài Math gate này, trừ khi chúng
đạt accepted độc lập và roadmap lúc đó yêu cầu bổ sung.

Riêng Graph đang có disposition `keep_off_technical_limit`: phải tiếp tục OFF,
không chạy lại cùng thiết kế và không được thêm vào matrix chỉ vì có một kết quả
accepted chung chung. Chỉ được mở lại sau khi owner duyệt thay đổi provider,
data-plane, corpus hoặc product scope và có declaration mới đúng phạm vi.

Trước khi chạy, matrix fixture phải bind final source commit, activation bundle,
runtime/deployment identity, snapshot và restore receipt, provider configuration,
corpus/collection fingerprint, test manifest và rollback target. Fixture được
chuẩn bị không phải permission để chạy; execution vẫn cần gate và owner
authorization hiện hành tại thời điểm đó.
