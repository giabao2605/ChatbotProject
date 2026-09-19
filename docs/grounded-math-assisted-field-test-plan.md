# Kế hoạch Grounded Math assisted field test 100 request

## Kết luận

Có thể tổ chức một chiến dịch 100 câu hỏi Grounded Math qua transport `internal_rag_sse` đã khai báo trên Math-only runtime để kiểm tra thực tế có kiểm soát. Kết quả phải được gọi là **owner-authorized operator controlled field test**, không được gọi là organic traffic hoặc UI parity.

100 request chỉ được tính vào ngưỡng pilot khi `bao.nguyen` ký declaration **trước khi chạy full campaign**, chấp nhận rõ nguồn assisted là một phần của pilot volume. Không được quyết định hồi tố dựa trên việc kết quả xanh hay đỏ.

Kế hoạch này ghi contract prospective `grounded-math-3d-100-v1`; implementation phải bind marker này trong declaration/window/manifest và cả operator/canonical gate. Quality, safety, security, citation, provenance, budget, rollback và default-rollout threshold không đổi.

## Trạng thái sau burst — 2026-08-14

- Window-06 đã tombstone ở `17` transport completion, `10` eligible trace và `7` calculation-invalid; không carry-forward request hoặc runtime duration.
- Burst `20dfd03b262a2eedf15731d7` hoàn tất `100/100` request nhưng canonical gate `rejected` ở `owner_declaration` do hash-domain mismatch. Nó chỉ là throughput evidence, không tính vào pilot và không được rerun.
- Validator đã được sửa cho campaign tương lai tại `d77f28c09c995dd983591a730db0e07a79b2f4e2`; fix không thay đổi tombstone hoặc authorization lịch sử.
- Disposition lịch sử vẫn là `deferred_pending_valid_pilot`, artifact SHA-256 `55007d64095d95005cb696f38c6a00e326624b5c8b024b7db6bf512df910f21e`. Owner authorization mới chỉ mở future pilot theo `grounded-math-3d-100-v1`; owner review, interaction matrix và default rollout vẫn bị chặn cho tới khi pilot/gate tương ứng pass.
- Runbook chuẩn bị review nằm ở `docs/grounded-math-owner-review-preparation.md`; tài liệu này không cấp quyền khóa sample hoặc bắt đầu review.

## Hardening cho operator window kế tiếp

Owner đã authorize prospective campaign `grounded-math-3d-100-v1` ngày `2026-08-14`; không sửa, nới hoặc tái sử dụng contract của window-06 hay burst-window-11. Operator traffic vẫn là `owner_authorized_operator_generated`, không phải organic demand, quality evidence hay UI parity; default rollout tiếp tục OFF.

Window-06 sau đó đã được dừng fail-closed trước card 018: `17` transport completion chỉ tạo `10` eligible trace, còn `7` trace có `calculation_result_status=invalid`. Không request hoặc runtime duration nào được carry-forward. Các invariant UI-assisted lịch sử ở phần dưới không áp dụng cho operator replacement; replacement dùng transport `internal_rag_sse` đã khai báo và không được claim UI parity.

- Trước mỗi dispatch, runner phải đọc `release_decisions.json` hiện tại và dừng nếu ledger không còn `incomplete` hoặc Grounded Math đã có decision.
- Trước card đầu tiên, canonical base gate phải bind đúng contract/window, provider smoke hợp lệ, `eligible_trace_count=0` và trace hash rỗng. Từ card thứ hai, tập trace hash completed trong WAL phải khớp chính xác `eligible_trace_count` và `trace_id_sha256` của base gate hiện tại; sáu check security/citation/provenance/budget/provider/leakage phải xanh. Gate cũ, thiếu marker hoặc thiếu trace dừng trước dispatch.
- URL runtime dùng chung một validator loopback chặt: chỉ HTTP tới literal `127.0.0.1`, không DNS alias, userinfo, path, query, fragment, proxy hay redirect.
- WAL ghi thời điểm terminal thực tế, không sao chép timestamp bắt đầu. Mọi started không có terminal hoặc transport exception vẫn là ambiguous và không được retry.
- Companion gate bắt buộc cả hai pacing cap: tối đa 3 attempt trong mọi rolling 30 phút và 35 attempt trong mọi rolling 24 giờ.
- Owner declaration phải machine-bind `divide=corpus_missing_dimensionless_divisor` và `sum=transport_has_no_explicit_document_scope`; không được lặng lẽ coi hai operation này là đã bao phủ.
- Inventory preflight đọc read-only `MaHang`, `SoLuong`, `Unit`, page và source-row identity để chỉ nhận mã kỹ thuật số-chấm không mang ngữ nghĩa, loại dòng thiếu quantity, part code có nhiều quantity fact, cặp khác unit và mọi candidate mà production `solve_grounded_calculation` không trả `valid`. Quantity/unit chỉ tồn tại trong bộ nhớ và inventory hash; public manifest và prompt không chứa giá trị.
- Prompt operator giữ document anchor chuẩn rút từ filename kỹ thuật đã allowlist, chỉ gồm drawing/version/model để retrieval bám đúng document và không chèn phần filename tự do; runner truyền đúng hai explicit part code từ field riêng `part_ids` đã hash-bind qua `current_part_ids`, không parse code từ toàn prompt. Corpus recapture hiện cho phép đúng 100 pair-intent card trên 4 document, với cap được khai báo `26` card/document và `7` card/document/operation; selection bias này không được dùng làm quality claim.
- Review contract chỉ có một primary human reviewer: `bao.nguyen` review đủ 20 case phân tầng và mọi failure/low-confidence; Codex chỉ hỗ trợ metadata và kỹ thuật.

Corpus phải được recapture trước mỗi campaign. Snapshot operator gần nhất dùng 12 PDF/130 BOM row; các con số 9 document/41 row và 7 PDF/87 row bên dưới là checkpoint lịch sử, không phải inventory mặc định cho window mới.

Các mục “Định nghĩa traffic”, “Các invariant bị khóa” và “Nguồn dùng để tạo challenge card” bên dưới mô tả assisted-UI contract lịch sử. Owner authorization mới thay contract đó đúng phạm vi operator replacement như phần hardening trên; không thay đổi default rollout hay human-review requirement.

## Checkpoint thực thi 2026-08-05 (lịch sử, superseded bởi trạng thái sau burst)

- Owner đã duyệt và restore thành công hai target riêng `Mech_Chatbot_DB_RestoreTest_RAGPilot_20260804_b437b32` và `TaiLieuKyThuat_v2_RestoreTest_RAGPilot_20260804_b437b32`; pre-ingest reconciliation receipt SHA-256 `f4a55bdfd3f7efe393e6a024c02436a43376d54aa2052c5d8fb702560b33c59d`, snapshot fingerprint `4f3bc3ad3e149d428f80db62569a705ceb3a098049930faefb96a82415511d97`.
- Tại checkpoint này, SQL không có pending migration; Qdrant không thiếu index/payload field. App `8180`, pilot `8200` và control `8210` khi đó healthy trên target 7 PDF `20260804_b437b32`; pilot chỉ bật Grounded Math, control giữ `all_off`.
- Corpus manifest đã khóa 33 PDF unique, 8 BOM-positive, 4 `plot.log` bị loại; manifest SHA-256 `aa8ee132ad25687fde63a619fc200c60906a7b6e654d2c188ce9f93d22883d1e`.
- Ingest proof đầu tiên bị quality gate chặn: `failed`, quality `50`, `0` chunk, `0` BOM record. Qdrant đã rollback về đúng `231` point; không có tài liệu mới pending-review, published hoặc servable. 32 PDF còn lại chưa được tạo job.
- Nguyên nhân gốc đã xác nhận là production worker chạy runner mà không bind repository runtime: managed `proxyllm` profile thực tế vẫn tồn tại trong target nhưng lookup fail-closed; đồng thời classifier truyền `db_engine` vào `get_department_domain_profile()` dù hàm này chưa nhận tham số đó. Artifact fail-closed: `.local/assisted-field-test-b437b32/ingestion-quality.json`, SHA-256 `aad03bda62c3df11b8aa82d45e453655fb1ea4485483c8e872c523091ca2cebf`.
- Root bug đã được sửa test-first trên RC `ab25cec413c4cda7b3543b4516a21e001ab6d1b6`: worker bind explicit SQL/Qdrant repository runtime cho toàn lifecycle, fail-closed nếu dependency thiếu và domain-profile lookup nhận explicit engine. Focused regression `122` test pass; full backend `2662 passed, 22 skipped`; coverage `92.37%`. Failed proof `pdf-07` vẫn được giữ nguyên làm tombstone.
- Hai target đã duyệt được reconcile read-only cho RC mới. Migration ledger `43/43` và Qdrant schema đều current; Qdrant source/target giống nhau `231` point. SQL serving giống nhau sau khi khai báo loại đúng một `dbo.TaiLieu.FilePath` là đường dẫn máy cục bộ của proof cũ; không có drift lifecycle/publication/content. Receipt `.local/restore-drill/restore-drill-20260804-ab25cec-reconciled.json`, SHA-256 `f4a55bdfd3f7efe393e6a024c02436a43376d54aa2052c5d8fb702560b33c59d`, snapshot fingerprint `4f3bc3ad3e149d428f80db62569a705ceb3a098049930faefb96a82415511d97`.
- Historical pre-publication proof `pdf-10` đã pass qua đúng production `run_worker()`: `pending_review`, quality `85`, `8` chunk, `12` BOM record, Qdrant `231 -> 239`; artifact `.local/assisted-field-test-b437b32/ingestion-proof-rc-ab25cec.json`, SHA-256 `10749973081555e38ab0805f5725bbf9f21cbfc87674d52af5dc35661ecd6ab5`. Sau đó document này đã được đưa vào batch publish 7 tài liệu ở dòng kế tiếp.
- Sáu BOM-positive còn lại (`pdf-11`, `pdf-16`, `pdf-20`, `pdf-25`, `pdf-32`, `pdf-33`) đã được ingest tuần tự qua cùng worker RC; cả 6 đạt quality `85`, tổng thêm `42` chunk và `75` BOM record. Sau validation contract, actor `demo_approver_technical` (ID 37, `knowledge_approver`) đã publish đủ 7 document BOM-positive trên target assisted; cả 7 là `approved/published/servable`. Publication artifact `.local/assisted-field-test-b437b32/publication-rc-ab25cec.json`, SHA-256 `643ef9742ec03bcc5a532cf23ef761ae6fde83e053daca0b62eb0ef194a04e15`.
- Runtime drift cũ đã được tombstone; 7-PDF window lịch sử bắt đầu `2026-08-04T09:08:38.4917915Z`, minimum runtime đến `2026-08-11T09:08:38.4917915Z`. Health binding tại checkpoint này khớp, nhưng canonical gate vẫn `rejected`, `eligible_trace_count=0`.
- Challenge manifest 99 card cũ được giữ làm artifact lịch sử nhưng bị supersede vì giả định có một starting trace được include. Window tại checkpoint này là `0/100`, nên không dùng manifest đó cho proof hoặc campaign; phải freeze manifest 100 card mới trước proof.
- Attempt `20260805-1` giữ tombstone ProxyLLM 503. Attempt `20260805-2` có một generation thành công qua production UI, 0 retry, nhưng không có `grounded_math_generation`, được exclude và không tính vào pilot. Proof batch 5 request vẫn chưa chạy.

## Vì sao cần plan riêng

- Pilot Grounded Math prospective yêu cầu đúng 100 request có calculation route, tối thiểu 72 giờ từ dispatch eligible đầu tiên đến completion eligible thứ 100, automated checks đủ 100 và human review 20 case.[S1][S2]
- Gate hiện đếm mọi trace có `execution_context=production` khi có đúng một `grounded_math_generation` hoặc `pilot_request_evidence` với `route=calculation`; gate không có trường phân loại organic/assisted.[S2][S3]
- Vì vậy assisted traffic có thể được gate đếm về mặt kỹ thuật, nhưng chỉ owner declaration mới quyết định nó có được dùng làm pilot evidence về mặt governance hay không.
- Historical disposable target ban đầu chỉ có 41 BOM row có quantity, thuộc 9 current/published document, 9 page, 31 distinct part code và 4 unit; chỉ 3 document là PDF giống production, 6 document còn lại là demo/eval Markdown. Historical corpus expanded sau publish có 7 production PDF, 87 BOM row, 40 distinct part-code identity toàn corpus (44 theo từng document), 19 description identity và 0 unit; `divide` được khai báo unavailable. Các số này chỉ mô tả window cũ. Campaign mới phải recapture inventory và vẫn xem 100 request là repeated exposure có phân tầng trên corpus nhỏ, không phải 100 tình huống tài liệu độc lập hay đại diện đầy đủ cho production.

## Định nghĩa traffic

| Loại | Định nghĩa | Có được gọi là organic | Mặc định tính vào 100 |
|---|---|---:|---:|
| Organic | Người dùng hỏi vì nhu cầu công việc đang phát sinh, không theo campaign card | Có | Có, nếu gate xác nhận eligible |
| Assisted controlled field test | Người kiểm thử được giao document/operation/task intent, tự viết câu hỏi như một người dùng; không biết quantity hay expected answer | Không | Chỉ khi owner predeclare `count_toward_pilot=true` |
| Synthetic/replay | Script/API loop, replay prompt, copy case eval, hoặc tạo câu hỏi từ quantity/answer/formula đã biết | Không | Không |

Việc một request đi qua production UI chưa tự động biến nó thành organic. Nguồn khởi tạo intent mới là điểm phân biệt.

## Các invariant bị khóa

1. Chỉ dùng Math-only pilot runtime và control `all_off` được bind trong window mới; operator gọi đúng transport loopback `internal_rag_sse`, không replay hoặc script loop ngoài governed runner.[S6]
2. Giữ exact commit, deployment, bundle, restore receipt, snapshot, provider configuration, SQL database và Qdrant collection của window được declaration bind.[S2][S6]
3. Giữ đúng contract `grounded-math-3d-100-v1`: 100 eligible, tối thiểu 72 giờ, một calculation, không provider retry, tối đa một final generation, latency multiplier `1.25`, cost multiplier `1.5`.[S2]
4. Artifact campaign chỉ lưu ID/hash/count/boolean/reason code; không lưu raw question, raw answer, raw document, credential hoặc private response.[S2][S7]
5. Không dùng quantity, expected answer, expected formula hoặc evaluation oracle để tạo câu hỏi.
6. Không lặp nguyên văn câu hỏi; không gửi song song; không cố gửi bù đến khi đủ con số sau khi một stratum thất bại.
7. Default rollout vẫn `all_off`; controlled field test không tạo live authorization.[S8]

## Nguồn dùng để tạo challenge card

Được dùng:

- document ID/hash và loại tài liệu;
- tên/mã tài liệu hoặc các token định danh mà người dùng có thể nhìn thấy;
- part code/description để chỉ định operand;
- operation intent: sum, add, subtract, ratio, percent, multiply, divide;
- vai trò/site/language style của cohort đã có quyền truy cập.

Không được dùng:

- BOM quantity;
- kết quả, công thức hoặc đáp án tham chiếu;
- output của lần chạy trước để viết lại câu hỏi kế tiếp;
- raw content ngoài phạm vi người dùng được phép xem;
- prompt/case từ formal evaluation pack.

Code hiện nhận diện bảy nhóm operation trên và chỉ tạo plan từ operation cùng operand label được nêu trong câu hỏi; tổng toàn BOM có thể không cần nêu operand.[S4] SQL BOM lookup vẫn áp dụng lifecycle, RBAC, department/site/security và current/published predicates.[S5]

## Thiết kế campaign

### Đơn vị thử nghiệm

Mỗi challenge card chỉ chứa metadata:

```text
campaign_id
card_id
document_identity_sha256
document_class: production_pdf | demo_eval
operation
operand_style: document_aggregate | part_code | description
operand_identity_sha256
operand_count
language_style
scheduled_batch
```

Với non-aggregate operation, authorized operator phải lấy user-visible operand label/code từ đúng authorized UI/source inventory đã freeze; card artifact chỉ giữ `operand_identity_sha256` và `operand_count`, không giữ raw label. Không có operand identity thì card chỉ được dùng cho document aggregate. Prompt được người kiểm thử soạn tại thời điểm chạy và nhập trực tiếp vào UI. Không ghi operand label, prompt hoặc response vào campaign artifact.

### Phân tầng 100 request

Corpus snapshot phải được inventory lại read-only trước declaration. Allocation chính xác được khóa trong manifest; các trần sau là bắt buộc:

- tổng cộng đúng 100 planned submissions;
- không quá 26 request trên một document;
- không quá 7 request cho cùng cặp `document + operation`;
- mọi published/current/approved/servable PDF có deterministic-valid pair trong inventory freeze phải được phủ; campaign gần nhất có 4 document đủ điều kiện;
- bao phủ mọi operation khả thi trong `sum/add/subtract/ratio/percent/multiply/divide`; operation không khả thi do corpus phải được nêu trong declaration, không được thay lặng lẽ;
- operator replacement chỉ dùng explicit part-code operand đã hash-bind;
- câu hỏi phải khác về nhu cầu diễn đạt, không chỉ thay một từ đồng nghĩa để né duplicate.

100 là số submission đã predeclare, không phải cam kết 100 submission đều trở thành eligible trace. Request không đi vào calculation route vẫn được giữ trong attempted count; không được spam thêm bản sao để bù.

### Nhịp chạy chống spam

- concurrency luôn bằng 1;
- tối đa 35 submission trong mọi rolling 24 giờ;
- tối đa 3 submission trong 30 phút;
- từ dispatch eligible đầu tiên đến completion eligible thứ 100 tối thiểu 72 giờ;
- sau mỗi request chờ transport `internal_rag_sse` hoàn tất và health/gate metadata được ghi nhận trước request kế tiếp;
- một operator không được copy/paste một template 100 lần.

Nhịp tham chiếu là lịch 100 card freeze đều trên 72 giờ (`72h / 99` khoảng 43 phút 38 giây); Scheduled Task poll 5 phút có thể làm thời gian thực dài hơn nhưng không được catch-up. Đây là giới hạn vận hành campaign, không phải threshold release mới.

## Trình tự thực hiện

### Bước 0 — Inventory và freeze manifest

1. Kiểm tra read-only pilot/control/app health.
2. Recapture corpus counts: document, page, BOM row, distinct part code, unit và document class; chỉ lưu count/hash.
3. Nếu fingerprint khác window hiện tại, dừng; không tạo manifest.
4. Capture starting trace ledger trước proof: exact `pilot-gate.json` hash, `eligible_trace_count`, toàn bộ `trace_id_sha256` đã có và owner disposition cho từng source class. Trace cũ không chứng minh được nguồn thì mặc định excluded.
5. Sinh challenge manifest 100 card từ metadata được phép, khóa SHA-256.
6. Với non-aggregate card, xác nhận operator có authorized operand label tương ứng với `operand_identity_sha256` nhưng manifest không giữ raw label.
7. Chạy duplicate check trên `document_identity_sha256 + operation + operand_identity_sha256 + operand_style + language_style`.

Output: `corpus-inventory.json`, `starting-trace-ledger.json`, `challenge-manifest.json`; không có raw document/operand/question/answer.

### Bước 1 — Proof batch 5 request

Trước proof, owner ký declaration riêng với `count_proof_toward_pilot=false`. Chọn 5 card có document/operation pair khác nhau, trong đó có ít nhất 2 production-like PDF document. Chạy thủ công, tuần tự qua production UI.

Proof chỉ xác minh:

- UI thật đi đúng pilot deployment/runtime identity;
- collector nhận production evidence;
- card không cần quantity/answer oracle;
- metadata artifact không chứa raw question/answer;
- stop condition và review flow thực sự dùng được.

Proof không dùng để tối ưu prompt theo đáp án. Chỉ được sửa challenge manifest nếu phát hiện lỗi cơ học như card trùng, document identity không tồn tại hoặc operation không thể biểu đạt từ metadata; mọi delta phải có hash mới và được owner xem trước.

Vì gate hiện không có exclusion flag, 5 proof trace có thể xuất hiện trong raw `eligible_trace_count`. Canonical acceptance phải trừ các `trace_id_sha256` nằm trong proof exclusion ledger, hoặc mở window sạch sau proof. Không được coi raw gate count là đủ nếu excluded trace vẫn nằm trong đó.

### Bước 2 — Owner declaration trước full 100

Owner xem proof artifact, corpus inventory và exact manifest hash, rồi chọn một trong hai disposition:

```json
{
  "traffic_class": "assisted_controlled_field_test",
  "count_toward_pilot": true,
  "organic_claim_allowed": false,
  "proof_batch_excluded": true,
  "campaign_manifest_sha256": "<sha256>",
  "corpus_inventory_sha256": "<sha256>",
  "starting_trace_ledger_sha256": "<sha256>",
  "pilot_window_sha256": "<sha256>",
  "git_sha": "<exact commit>",
  "deployment_id": "<exact pilot deployment>",
  "runtime_identity_sha256": "<sha256>",
  "snapshot_fingerprint": "<sha256>",
  "activation_bundle_sha256": "<sha256>",
  "restore_evidence_sha256": "<sha256>",
  "sql_database": "<exact disposable target>",
  "qdrant_collection": "<exact disposable target>",
  "owner": "bao.nguyen",
  "signed_before_full_campaign": true
}
```

- `count_toward_pilot=true`: eligible trace từ exact manifest được cộng vào pilot volume, nhưng final report vẫn tách `assisted_eligible` và `organic_eligible`.
- `count_toward_pilot=false`: campaign chỉ là field/soak evidence; final pilot acceptance dùng `raw eligible - excluded campaign trace hashes`, dù raw gate có thể hiển thị 100.

Declaration không được sửa sau khi biết campaign result. Nếu manifest/corpus/runtime đổi, declaration hết hiệu lực.

Window lịch sử tại checkpoint bắt đầu ở `0/100`; provider smoke và proof batch đều excluded. Vì vậy một future full campaign phải predeclare đúng 100 submission mới. Chỉ khi cả 100 đều eligible thì campaign mới tự đạt ngưỡng 100; mọi noneligible vẫn giữ nguyên disposition và không được gửi bù ngoài manifest.

Khuyến nghị: chỉ chọn `true` khi proof xác nhận intent có thể được tạo mà không dùng oracle, manifest đạt diversity caps và owner chấp nhận rõ giới hạn của inventory vừa recapture cùng mọi operation unavailable. Mốc 7 production PDF/87 BOM row và `divide` unavailable chỉ thuộc historical window; campaign mới không được kế thừa các số đó. Nếu không, chọn `false` và chờ organic traffic hoặc mở pilot mới với corpus tốt hơn.

### Bước 3 — Full campaign 100

1. Mỗi ngày kiểm tra health/runtime binding trước batch.
2. Chạy đúng card được schedule, tuần tự qua transport `internal_rag_sse`; không dùng UI parity làm bằng chứng.
3. Sau mỗi request chỉ ghi metadata: card ID, started/completed timestamp, transport completion class, route eligible boolean, runtime identity hash, trace hash và automated check booleans.
4. Không mở raw response để lấy operand/quantity cho card sau.
5. Cuối ngày chạy gate hiện hành; không sửa threshold hoặc artifact lịch sử.
6. Nếu card không eligible, giữ kết quả `noneligible`; không lặp lại card và không thay bằng prompt gần giống.

### Bước 4 — Review 20 case

Khóa sample trước khi đọc câu trả lời:

- đủ 20 primary case cho `bao.nguyen` theo signed `single_owner` contract;
- phân tầng theo document class, operation và eligible/noneligible outcome;
- mọi failure, access-denied bất thường hoặc low-confidence case bắt buộc owner review;
- cùng document-operation không chiếm quá 2 primary case nếu còn strata khác.

`tran.nghi` chỉ thực hiện technical review ở interaction-matrix/release step sau pilot; không được tính là primary human reviewer của 20-case operator sample.

Review có thể xem response trong authorized UI, nhưng artifact chỉ lưu reviewer, trace hash, booleans `calculation/formula/unit/citation/provenance/correct`, confidence band và reason code. Không copy raw answer hoặc raw document vào report.[S1]

### Bước 5 — Quyết định

Campaign chỉ được ghi là completed khi:

- exact 100 planned cards đã có disposition;
- không có undeclared replacement/duplicate;
- owner declaration predates full campaign;
- proof exclusion và assisted/organic counts được reconcile bằng trace hash;
- gate checks giữ nguyên;
- 20-case review hoàn tất.

Campaign không tự authorize default rollout. Nếu pilot đủ tối thiểu 72 giờ, canonical eligible count đạt 100 theo declaration, automated gate pass và review pass, bước tiếp theo vẫn là interaction matrix c1/c5, technical review bởi `tran.nghi`, rồi owner decision/default-rollout ledger như plan chính.[S1]

### Nhánh owner-authorized burst 100 request

Khi owner chấp nhận chạy nhanh, operator có thể tạo một campaign riêng với traffic class `owner_authorized_operator_generated_burst` và gửi tối đa 100 request tuần tự trong một invocation. Contract bắt buộc giữ concurrency `1`, không retry/replacement, append-only WAL, kiểm tra live health, release ledger và trace reconciliation sau từng request; bất kỳ ambiguous, noneligible, drift hoặc automated-check failure nào cũng dừng trước request kế tiếp.

Burst declaration phải được ký trước request đầu tiên và luôn ghi `count_toward_pilot=false`, `qualifies_as_7_day_pilot=false`, `duration_claim_allowed=false`, mọi organic/quality/UI/default-rollout claim đều false. Kết quả chỉ là throughput/safety evidence tại một thời điểm; dù đạt 100/100, nó không chứng minh ổn định theo thời gian và không hoàn thành contract prospective 72 giờ. Không tạo Scheduled Task cho nhánh này; runtime phải dừng và campaign phải tombstone sau khi kết thúc hoặc fail.

## Stop conditions

Dừng ngay và report fail-closed, không tự restart hay sửa runtime, khi có một trong các điều kiện:

- leakage, RBAC/site/security violation;
- citation/provenance sai hoặc stale/pending data được serve;
- commit, deployment, runtime identity, snapshot, provider, bundle, restore hoặc collection drift;
- provider retry/fallback/error ngoài declaration;
- raw question/answer/private document lọt vào campaign artifact;
- operator phát hiện card được tạo từ quantity/answer/formula;
- duplicate card hoặc burst vượt pacing cap;
- proof không tạo được production calculation evidence từ metadata-only challenge;
- current gate parse error hoặc producer evidence thiếu;
- rollback/health failure theo plan chính.

Wrong calculation hoặc severe wrong-answer dừng campaign và giữ evidence. Provider outage làm batch inconclusive, không được rerun để chọn kết quả đẹp.

## Optional path — bổ sung corpus tài liệu thực tế

Trong historical `b437b32` window, thêm tài liệu thực tế sẽ cải thiện diversity hơn việc ép 100 biến thể trên 9 document khi đó. Không được dùng các số lịch sử này để lập campaign mới; phải recapture inventory. Việc ingest vào chính target đã freeze sẽ làm window, restore reconciliation và runtime binding mất hiệu lực.[S2][S6]

Read-only preflight ban đầu của bốn folder `9.1.00678`, `9.3.03843`, `9.3.04068`, `9.3.04080` ghi nhận 33 PDF unique, không mã hóa, mỗi file một trang, text-extractable, không duplicate hash hoặc symlink. Visual + text-extraction QA chỉ xác nhận 8 PDF có bảng BOM rõ ràng: lần lượt `0`, `3`, `2`, `3` theo bốn folder; 4 file `plot.log` phải bị loại. Vì vậy corpus mở rộng có thể tăng số scenario thực tế nhưng không được coi cả 33 PDF là Grounded Math case. Exact file manifest và folder hash phải được lưu trước ingest; chỉ 8 BOM-positive PDF được dùng để thiết kế math challenge.[S9]

Nếu owner chọn path này:

1. Chỉ inspect folder read-only và lập manifest file/hash/type trước.
2. Chạy ingestion quality/preflight trên scope riêng; không đụng current pilot target.
3. Owner duyệt exact tên disposable SQL database và Qdrant collection mới.
4. Ingest vào target mới, recapture fingerprint, restore/reconciliation receipt và freeze RC/window mới.
5. Tạo lại corpus inventory, challenge manifest, proof batch và declaration trên exact fingerprint mới.
6. Không auto-cleanup và không chuyển default rollout.

Hai lựa chọn không được trộn evidence:

- tiếp tục historical small-corpus campaign: nhanh hơn nhưng kết luận bị giới hạn bởi snapshot 9 document/41 BOM row của window đó;
- mở corpus-expanded pilot mới sau recapture: chậm hơn và reset window, nhưng tạo bằng chứng diversity đáng tin hơn.

## Artifact tối thiểu

```text
assisted-field-test/
  corpus-inventory.json
  starting-trace-ledger.json
  challenge-manifest.json
  proof-declaration.json
  proof-batch.json
  owner-declaration.json
  daily-batch-<date>.json
  campaign-summary.json
  review-20.json
```

Governed Python runner/gate hiện có chịu trách nhiệm hash, sort, validate JSON và dispatch fail-closed; wrapper PowerShell chỉ orchestration bằng absolute path, không tự diễn giải contract.

## Nguồn primary

- **S1** — `docs/advanced-rag-value-first-rollout-plan.md`: checkpoint hiện tại, Phase 2, contract `grounded-math-3d-100-v1`, review 20 case, stop conditions và interaction matrix.
- **S2** — `scripts/ops/grounded_math_pilot_gate.py`: canonical validator hiện hành cho contract marker, exact 100 trace, tối thiểu 72 giờ, runtime/provider bindings, fail-closed checks và hashed trace IDs.
- **S3** — `C:/Users/bao.nguyen/Documents/ChatBotProject-math-pilot-evidence/src/mech_chatbot/rag/execution.py:580-610` và `src/mech_chatbot/rag/pipeline_steps.py:683-702`: producer của calculation evidence và grounded math generation.
- **S4** — `C:/Users/bao.nguyen/Documents/ChatBotProject-math-pilot-evidence/src/mech_chatbot/rag/grounded_math.py:47-89,156-215,237-340`: exact-document selection, supported operation và deterministic calculation plan.
- **S5** — `C:/Users/bao.nguyen/Documents/ChatBotProject-math-pilot-evidence/src/mech_chatbot/db/repositories/bom.py:138-220`: governed BOM lookup, document scope, RBAC và current/published predicates.
- **S6** — `C:/Users/bao.nguyen/Documents/ChatBotProject-math-pilot-evidence/.local/math-pilot-b437b32-live-v2/pilot-window.json`, `pilot-gate.json` và `reports/grounded-math/20260804-selective-b437b32/review-governance-single-owner.json`: historical runtime/window/count/governance bindings, chỉ dùng đối chiếu và không được carry-forward.
- **S7** — `C:/Users/bao.nguyen/Documents/ChatBotProject-math-pilot-evidence/tests/unit/test_grounded_math_pilot_gate.py:298-313`: regression proof rằng gate artifact hash trace IDs và không render raw trace content.
- **S8** — `data/integrated_hardening_v1/release_decisions.json:1-8`: default release decisions vẫn incomplete và Grounded Math chưa có accepted default decision.
- **S9** — `reports/grounded-math/20260804-assisted-corpus-audit.json` (SHA-256 `93f4e5d427a598df65a24c68a877a10d50666110d2127298753a6db6f149a354`): metadata-only audit của bốn root folder, visual confirmation 8 BOM-positive PDF và explicit `ingestion.authorized=false/current_pilot_target_allowed=false`.
