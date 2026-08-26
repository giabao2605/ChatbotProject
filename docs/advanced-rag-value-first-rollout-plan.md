# Kế hoạch triển khai Advanced RAG theo hướng value-first

## Tóm tắt

- Default rollout nội bộ hiện dùng signed `selective` Math-only trên commit `67265a0`; chỉ `RAG_GROUNDED_MATH_ENABLED=true`, sáu feature còn lại giữ OFF.
- Tách Grounded Math, Query Decomposition, Graph Retrieval và CRAG + Claim Repair thành các capability được đánh giá, pilot và quyết định độc lập.
- Phát hành dần: tính năng đạt không phải chờ tính năng khác; tính năng chưa đạt tiếp tục OFF.
- Mỗi pilot chạy trên Windows/LAN riêng theo contract của capability và đủ 100 request đúng nhóm. Mặc định vẫn tối thiểu 7 ngày; riêng Grounded Math dùng contract prospective 3 ngày/100 request được owner chấp nhận ngày `2026-08-14`.
- Theo đến cùng bốn tính năng chính: Grounded Math, Query Decomposition, Graph Retrieval và CRAG + Claim Repair. Chỉ dừng khi `accepted` hoặc chứng minh kỹ thuật rằng muốn tiến xa hơn phải phá ngưỡng đã khóa.
- Community Summaries chỉ bắt đầu sau Graph accepted. Late Interaction giữ OFF vô thời hạn.

## Checkpoint Query controlled-demo pilot preparation — 2026-08-26

- Formal Query-only evidence trên `fe4dc37647b8078a2df4a73459c8ef65929b6de8` đã hoàn tất technical gate `3/3`, provider path `111/111` và human review `39/39`. Evidence này chỉ chứng minh quality; không tự authorize activation, runtime, traffic, pilot hoặc default rollout.
- Activation bundle lịch sử bind `c22411a9be6c5edfd6869bb1c71dfc2add5f5864`; bundle đó không được dùng cho commit mới chứa pilot harness. Commit mới phải có activation draft và bundle mới bind exact source commit.
- Offline pilot harness dùng contract `query-decomposition-24h-100-v1`: đúng `100` card freeze trước dispatch, từ card đầu đến card cuối đủ `24` giờ, concurrency `1`, zero retry/replacement/catch-up. WAL metadata-only khóa card và trace exactly-once; raw question, answer và trace ID không được lưu.
- Per-request gate chấp nhận câu trả lời đầy đủ hợp lệ hoặc terminal `evidence_gate` safe refusal. Mọi safe refusal, invalid/failure và tối thiểu `20` trace phân tầng phải được owner review; automated gate không thể tự cấp default rollout.
- Operator runbook và rollback giữ `.env`, Scheduled Task và Git remote nguyên trạng. Rollback dừng candidate runtime, chuyển về `all_off`, kiểm không còn flag bật và bảo toàn WAL/artifact.
- Gate kế tiếp được gom thành một consolidated approval bind exact clean source commit, activation draft, frozen schedule plan và thời hạn `24h10m–26h`. Approval đó chỉ cho finalizer materialize activation + pilot authorization; finalizer không start runtime, không phát provider traffic và không dispatch pilot. Actual runtime launch/traffic chỉ xảy ra ở bước operator launch sau khi exact consolidated approval đã được ghi nhận.
- Consolidated root `query-pilot-launch-edc92d3-20260826-01` đã consume exact approval nhưng dừng fail-closed tại `pilot_authorization_materialization`: canonical activation bundle có đủ bảy feature flags, còn pilot validator cũ chỉ chấp nhận map một key. Activation chỉ materialize một phần; pilot authorization, runtime, provider traffic và dispatch đều chưa xảy ra. Root và approval này là tombstone, cấm retry/reuse.
- Root fix đổi pilot validator sang canonical `selective` profile Query-only và regression dùng đúng full bundle shape. Lượt pilot kế tiếp bắt buộc clean commit, fresh consolidated draft/approval và never-used run root; không carry-forward partial materialization.
- Consolidated root `query-pilot-launch-50c4893-20260826-01` đã materialize activation và pilot authorization nhưng dừng fail-closed ở runtime health preflight, trước card đầu tiên. Control `all_off` không hợp lệ trong `controlled_demo`, còn candidate bị từ chối vì consolidated approval giữ fractional seconds trong khi derived authorization canonicalize về whole seconds. Cả hai process đã được dừng; control/candidate trace đều `0` byte, `0` pilot request và không có provider traffic. Draft SHA-256 `9cddfe528a30ac025062498e941b5f6ad00b03f3bc45ff73718e079b29344c84` cùng approval SHA-256 `8245fdaa1e115606ee4eaaab1d70b4ce0e5bce0cef1084bd1916717fa2a75f03` và root này là tombstone, cấm retry/reuse.
- Root fix kế tiếp dùng candidate-only supervisor, bind exact operator runner vào consolidated draft, so sánh approval window theo canonical timestamp, khóa exact source/bundle/authorization/manifest/schedule/runtime identity/SQL/Qdrant/feature map, claim trước egress và luôn stop runtime trong `finally`. Chỉ sau clean commit, regression và independent review mới được tạo một fresh consolidated draft trên never-used root; chưa có quyền start runtime hoặc phát provider traffic trước approval mới đó.
- Consolidated root `query-pilot-launch-1d8de31-20260826-01` đã materialize activation và pilot authorization nhưng dừng ở offline operator-input validation trước khi start runtime: helper trả authorization SHA-256 dạng chuỗi, còn operator gọi lại hàm hash bytes nên phát sinh `TypeError`. Read-only SQL/Qdrant snapshot đã pass với SQL `Mech_Chatbot_DB`, collection `MechChatbot_CRAG_Eval_v1` và fingerprint `e4cfafa2d3a9f73ead1e2c40dacef5394c51e4fc9fa7658629d3d221361a586f`; `0` runtime process, `0` pilot request, `0` provider traffic. Draft `0ff5220bb4b348bffcb12041662b9530c199656d55dbde54e68b77f0f41f4073`, approval `401864407a909e9025b082430be5f6043a2081fc6506ab340bf83b0e43b0f77e` và root này là tombstone; không retry/reuse.

## Checkpoint Grounded Math default rollout và Query readiness — 2026-08-22

- Grounded Math đã hoàn tất campaign `19aacefbe67b1aa3907a490c` đúng `100/100`, owner review `20/20`, interaction matrix `64/64`, rollback live và same-bundle restart. Signed default-rollout ledger chỉ accept `RAG_GROUNDED_MATH_ENABLED`; bundle `selective` bind exact commit `67265a0bd6135f9f205521e99bd51870a955b014`. Control `8210` giữ `all_off`, candidate `8200` là Math-only; Scheduled Task `ChatBotProject-GroundedMath-Operator-Window13` đã `Disabled`.
- Query operator hardening đã freeze tuần tự tại `e8b6a52` (bounded shutdown và next-window guards), `d67aea9` (trace identity/failure fail-closed) và `a88c0bc08789d10d8dd913e6945e683e54863126` (phân biệt strict deterministic local split với fallback bị cấm). Baseline không được split; candidate chỉ chấp nhận split trong `evaluation` khi có đúng `2–3` intent, coverage đầy đủ, planner/token/cost bằng `0`, không overflow/deadline và không có fallback khác.
- Window `query-formal-a88c0bc-20260820-01` dừng trước provider traffic vì thiếu process binding `QDRANT_COLLECTION=MechChatbot_CRAG_Eval_v1`; window đã tombstone, không retry hoặc carry-forward.
- Window thay thế `query-formal-a88c0bc-20260820-02` bind cả `QDRANT_COLLECTION` và `RAG_EVAL_EXPECTED_COLLECTION`, preflight pass `13/13`, rollback pass `33/33`, nhưng fresh provider smoke duy nhất fail `0/5` với một timeout và bốn HTTP `502`, zero retry. Không tạo declaration/trace, không chạy formal pair và Query tiếp tục OFF. Authorization của window đã tiêu thụ; toàn bộ artifact chỉ là tombstone.
- Hardening Query đã freeze tại `58dbb08646b2b9e98f425571387e01704a955201`. Fresh run-root `query-provider-smoke-58dbb08-20260820-141914` pass cả offline preparation và provider-boundary revalidation, sau đó smoke fail `0/5`: cả năm request trả HTTP `502 InternalServerError`, zero retry, một attempt/request, timeout 30 giây, `provider_outcome.reason=non_capacity_failure`. Artifact SHA-256 `ca1a514833848684e1a4b34cae4e6ee9aebac275530f3a353adae597ac0e7e4b`; không có declaration, trace hoặc formal artifact. Run-root đã tombstone và authorization smoke đã tiêu thụ.
- Readiness hardening đã freeze tại `d28be0661269b71a910b3e7f1056c2fa5899f74a`: full unit `2974/2974`, targeted `111/111`, compile, PowerShell parse, secret scan và dependency audit đều đạt; Standards, Spec và Security re-review không còn finding actionable. Restore drill disposable pass ngay attempt đầu với SQL `ONLINE/MULTI_USER` và Qdrant `231/231`; browser health smoke pass root/API/assets, không gọi `/chat` hoặc tạo provider traffic. App gateway tạm đã dừng và port `8080` được giải phóng.
- Recovery signal bên ngoài đã được ghi nhận tuần tự: health smoke độc lập `0/5`, rồi `4/5`, sau cùng `5/5` trên `d28be06` với zero retry, P95 `3.13s`, provider configuration SHA-256 `9d978ec3fb533f7316eb98928ec0f3cbbde9f6e52b33ae3b45aff15d1d61416f`. Lượt `5/5` có artifact SHA-256 `01f4f2dd39502762ea0585518123a23e879f19c47f173f8b58affe78fd6b03a4` nhưng chỉ là `health_proof_only`, đã consumed và không được reuse làm formal evidence.
- Formal window `query-formal-b00f64b-20260821-01` trên exact commit `b00f64b7a4081bc923dffe998ba2e605d955ff23` đã pass provider-boundary preflight `13/13`, rollback `33/33` và fresh smoke `5/5`, zero retry. Pair 01 dừng fail-closed trước khi tạo run/gate/pair artifact vì candidate trace có `hybrid_fallback` tại case `decomp-three-source-compare`, phase `strict_exact`, lỗi `ResponseHandlingException`; baseline/candidate evaluation đã chạy nhưng quality không được adjudicate, pair 02/03 không chạy. Authorization và toàn bộ window đã consumed/tombstone; không được resume, retry, carry-forward hoặc reuse bất kỳ artifact nào.
- Trace diagnosis cho thấy ba retrieval branch Query Decomposition dùng chung Qdrant client và chạy chồng lấn. Design delta test-first tại `3d95c0031ce20595e226085effb91d3a631800cd` chỉ tuần tự hóa branch retrieval trong `_run_complex_plan` bằng `max_workers=1`; generic executor, timeout, retry, fallback/oracle, threshold và feature state không đổi. Regression mô phỏng chính `ResponseHandlingException` khi shared-client call overlap; full unit `2985/2985`, coverage `92.36%`, dependency audit sạch và Standards/Spec review không có finding. Tác động latency do tuần tự hóa phải được chứng minh lại trong formal window mới; Query vẫn OFF.
- Formal window `query-formal-3d10c28-20260821-01` trên exact commit `3d10c28d8605efa77534b39a61b9a684ead74349` đã pass offline và provider-boundary preflight `13/13`, rollback `33/33`, nhưng fresh smoke duy nhất fail `0/5`: cả năm attempt bị `ExternalProcessingDenied` trước provider call, zero retry, do process chưa bind explicit `EXTERNAL_PROCESSING_POLICY=all_external`. Đây không phải provider-health evidence; không tạo declaration, trace hoặc formal pair. Smoke SHA-256 `66eb4608c170367e3527ea746aefc51359f47f962c355e151a780ff93e261e2d`; disposition SHA-256 `5e658f729dcbe785362f00c610b7c567566de417a96ed6f367ceacb5d751b2dd`. Authorization và root đã consumed/tombstone; cấm sửa policy rồi rerun, retry, carry-forward hoặc reuse.
- Policy-binding hardening test-first tại `3be04e18cf0201f5b90362627b19d23de919b1f3` buộc provider smoke từ chối snapshot chưa explicit `all_external` trước khi build client; Query provider-boundary cũng từ chối và ghi `provider-boundary-policy-failure.json` để consume root. Regression chứng minh sửa policy sau failure vẫn không thể revalidate cùng root. Runbook bind policy trước Query boundary revalidation và trước CRAG smoke; không đổi provider hash, timeout, retry, oracle, threshold hoặc feature state. Full unit `2987/2987`, coverage `92.36%`, dependency audit sạch; Standards/Spec review không còn finding.
- Formal window `query-formal-d76ad9c-20260821-01` trên exact commit `d76ad9c64ab684f138f1c589e04b2e1f17707a2d` đã pass offline và provider-boundary preflight `13/13`, rollback `33/33`, rồi fresh smoke `5/5`, zero retry, một attempt/request. Declaration mới bind exact draft, authorization, operators, provider configuration, Query-only scope và Math release hashes; không reuse recovery signal hoặc artifact từ window cũ.
- Pair 01 là formal evidence hợp lệ và pass toàn bộ gate với latency P95 ratio `1.035668005`, cost ratio `1.235103061`; `technical_eligible=true` nhưng `production_eligible=false`. Trước khi Python của pair 02 khởi động, PowerShell không resolve được relative interpreter path. Sự kiện không tạo output/provider traffic và trace còn `0` byte, nhưng contract `stop_on_first_failure` không có ngoại lệ nên window phải terminal tại đây; chỉ có một formal pair hoàn thành.
- Corrected pair-02 launch sau terminal là out-of-contract, chỉ được giữ làm non-formal diagnostic residue và tuyệt đối không reuse/carry-forward. Raw observation của residue fail latency P95 ratio `1.511066968 > 1.5` dù zero provider failure/retry; số liệu này không phải formal/rollout evidence. Pair 03 không được tạo. Disposition SHA-256 `8c22265d301e37d7169ad8c28ad84a2b21bfdb5eaa00aac9004c8fbbc054c64d` được bảo toàn và canonical adjudication SHA-256 `3313e5dc4e3dec91b3c31d12976aca99cc6545e074bd6b42ceac8e3c767ec08e` sửa terminal reason thành pre-run dispatch failure. Authorization/window đã consumed/tombstone; Query tiếp tục OFF.
- Formal-dispatch hardening test-first tại `62dd9c66c4c1e47a01553abdb51290ceea9d1c9b` thêm `start_query_formal_pair.ps1`: relative/missing/unlaunchable Python bị chặn trước mutation; cùng resolved executable thực hiện probe, semantic pre-validation và đúng một rollout; trace/output được claim atomically trước arm đầu. Shared validator kiểm manifest 13-case, fresh provider smoke và rollback commit/flag cả trước trace lẫn ngay trước arms. Full unit `3001/3001`, touched rollout-module coverage `91%`, compile, PowerShell parse, secret scan và Spec/Standards/Security review đều pass; không tạo provider traffic. `pip-audit` phát hiện hai advisory mới nhưng không reachable qua code path hiện dùng (`cryptography` chỉ dùng Ed25519, không PKCS#7 decrypt; không có direct `h2` import); dependency remediation phải tách riêng và audit tổng thể chưa được gọi là sạch. Gate kế tiếp là một authorization draft/run-root/evidence/smoke/declaration/trace/series hoàn toàn mới trên clean commit; không reuse window `d76ad9c`.
- Owner đã approve exact draft SHA-256 `4146fdb19e5d507a964c9d79f16fc2e3b06960345cc98897c45726b72c8895b9` cho window mới `query-formal-0eaddfa-20260821-01` trên clean commit `0eaddfa3998ce4ee5cc2d17aa4c3b8e1e605bd90`. Offline preparation đầu tiên dừng trước khi tạo artifact vì `query_window_preparation_binding_drift`: packet `query-crag-offline-preparation.json` vẫn bind runner SHA-256 cũ `5bfdbaa23c8dd7902dcc4042d2c5c371683c1fe79a4662018373460716418e4a`, trong khi exact approved runner sau hardening có SHA-256 `4d970dbf0d8ff3aea68a56c808ce70239f958e6cd1930781ea16fe350d71e5f0`.
- Contract `stop_on_first_failure` làm authorization/window `0eaddfa` consumed và tombstone ngay tại offline pre-root validation. Không chạy provider-boundary, smoke hoặc formal pair; provider traffic `0`, không có declaration/trace/pair output, Query tiếp tục OFF. Disposition SHA-256 `9a3a9ded829e95605e09267404c1c77cc3b9f55612483047c533ee549ecaeda3`. Không được refresh packet rồi tiếp tục cùng authorization/root; formal attempt sau chỉ được mở sau design delta + regression mới, clean commit mới, draft/approval/root/preparation/boundary/smoke/declaration/trace hoàn toàn mới.
- Preparation-binding design delta test-first tại `d42462866b9d5905642bc518d34e29c18d7b7802` refresh packet sang exact runner SHA-256 `4d970dbf0d8ff3aea68a56c808ce70239f958e6cd1930781ea16fe350d71e5f0` và thêm regression fail nếu packet drift khỏi tracked runner. Targeted `27/27`, full unit `3002/3002`, compile, secret scan và Spec/Standards/Security review đều pass; không thay đổi timeout, retry, oracle, threshold hoặc feature state.
- Formal window mới `query-formal-d424628-20260821-01` trên exact clean commit `d42462866b9d5905642bc518d34e29c18d7b7802` pass offline/provider-boundary preflight `13/13`, rollback, fresh provider smoke `5/5`, zero retry, một attempt/request và declaration bind exact draft/authorization/runtime/operators/provider/Math hashes. Pair 01 pass toàn bộ gate: latency P95 `16748.04 → 11720.63` ms, ratio `0.699820994`; cost ratio `1.249319684`; `technical_eligible=true`, `production_eligible=false`.
- Pair 02 là terminal formal failure vì check duy nhất `latency_within_budget=false`: latency P95 `8414.83 → 21364.61` ms, ratio `2.538923543 > 1.5`. Cost ratio `1.211159875` và mọi non-latency gate đều pass; provider failure/retry và prohibited trace event đều bằng `0`. Contract dừng ngay, pair 03 không được tạo. Disposition SHA-256 `b66c871b67e43b0f017472743fb455d99d8f5a90d18c9621a98090e924c22419`; authorization/window đã consumed/tombstone, Query tiếp tục OFF và không artifact nào được retry, reuse hoặc carry-forward.
- Offline exact-gate replay tái tạo byte-for-byte gate pair 02 và xác nhận không có lỗi tính P95. Minimized tail case `decomp-three-intents` có branch retrieval tuần tự `2354 + 2467 + 2532 ms`, parent context `6089 ms` và generation `7736 ms`; thay tổng branch bằng ideal parallel max vẫn cho candidate P95 `16543.61 ms`, ratio `1.966006443 > 1.5`. Vì vậy hoàn tác serialization không đủ đạt gate và mở lại regression shared-client `ResponseHandlingException`; bỏ parent hydration hoặc cấp client riêng từng branch là thay đổi architecture/evidence semantics chưa có seam deterministic chứng minh an toàn. Diagnosis SHA-256 `8589ec36d4d3728755d79c8823715dbe2cad0df17b6367a25b8480c57bb8b25a` kết luận `keep_off_technical_limit_current_design`: không tạo speculative code fix/test, Query giữ OFF. Future scope chỉ được mở bằng design riêng cho batched/isolated branch retrieval + parent context, rồi clean commit và formal series hoàn toàn mới.
- Owner sau đó mở đúng future architecture scope nói trên. Design delta test-first tại `9e48ddcf6e9533958c241ac1ae68e2fa31507070` thay ba branch call tuần tự bằng dense batch và sparse batch có thứ tự qua `query_batch_points`; strict miss/BOM chỉ mở broad batch cho đúng branch cần thiết. Dense vẫn dùng query embedding, project-owned RRF, strict/broad/RBAC filter, lifecycle serving filter, mode và direct/simple retrieval contract được giữ nguyên. Batch failure hoặc deadline là terminal, không serial fallback, retry hay traffic bù.
- Parent hydration chỉ dùng `query_batch_points(query=None)` khi request thực tế có retrieval mode `decomposed_*`; baseline và simple/direct request giữ nguyên đường scroll cũ. Mỗi parent request lặp lại exact doc/parent key, site, department, security/clearance, published/approved/current, serving epoch và publication version. Batch failure dừng fail-closed, không scroll retry; timeout bị chặn bởi remaining request deadline và không tăng ngưỡng cũ.
- Regression khóa query-vs-document embedding semantics, strict hit/miss, BOM broad, general parity, lifecycle filtering, deadline trước/between batch, zero serial retry, parent order/scope và baseline compatibility. Full unit `3015/3015`, compile, `pip-audit --local` và ba review correctness/security/complexity đều pass; không tạo provider traffic. Telemetry đánh dấu branch latency là `shared_batch` và ghi một `retrieval_batch` aggregate để không diễn giải thành ba latency độc lập.
- Commit `9e48ddc` chỉ chứng minh design/offline safety, không chứng minh pair 02 sẽ qua gate. Với baseline pair-02 cũ, candidate phải `<=12622.245 ms`; counterfactual branch-only còn `16543.61 ms`, nên parent batch cần tiết kiệm xấp xỉ `3921.365 ms`, tương đương parent tail cũ `6089 ms` phải xuống khoảng `2167.635 ms` hoặc thấp hơn nếu các stage khác không đổi. Đây là planning target, không phải measured evidence. Query vẫn OFF; formal attempt kế tiếp bắt buộc clean commit mới, draft/owner authorization/never-used root/preparation/provider-boundary/fresh smoke/declaration/trace và tối đa ba pair tuần tự hoàn toàn mới.
- Owner approve exact draft SHA-256 `4f03b5909559248d3e961232e0fc25877b79c2b317cd61f8f26f3f251b2ee306` cho window `query-formal-0318ded-20260821-01`, nhưng operator gate dừng trước offline preparation khi kiểm expiry bằng `DateTimeOffset.Parse` trên giá trị đã bị `ConvertFrom-Json` materialize thành `DateTime` rồi format theo locale. Contract first-failure làm authorization/window consumed và tombstone; provider-boundary/smoke/declaration/formal pair đều chưa bắt đầu, provider traffic `0`, root chỉ giữ disposition SHA-256 `a37cbbd42147be03be16466cbbf8f1ff2545623affd9d98b72bea11ac3c7aa5d`. Không được sửa parse rồi tiếp tục cùng authorization/root.
- Authorization-gate hardening test-first tại `802aa900e17f9941aa0291bd885edf5f9f2bb94d` đưa exact owner authorization vào `prepare_query_formal_window.ps1`, parse ISO expiry không phụ thuộc locale, giới hạn lifetime tối đa 60 phút và ghi tombstone marker nếu authorization invalid/expired tại provider boundary. Regression chứng minh locale `vi-VN` pass với authorization hợp lệ, authorization quá 60 phút bị chặn trước root, expiry tại boundary làm corrected-expiry retry trên cùng root thất bại. Full unit `3018/3018`, PowerShell parse, compile, runtime dependency audit, secret scan và diff check đều pass; không tạo provider traffic. Formal attempt kế tiếp vẫn cần clean commit/draft/approval/never-used root/evidence/smoke/declaration/traces hoàn toàn mới.
- Formal window `query-formal-6a566e1-20260821-01` trên exact clean commit `6a566e17729022ea060d3a61ef65986b6b8562da` bind authorization lifetime đúng 60 phút, pass offline/provider-boundary preflight `13/13` và rollback với Query flag OFF. Fresh smoke duy nhất sau đó fail `0/5`: cả năm request trả HTTP `502 InternalServerError`, zero retry, một attempt/request, timeout 30 giây; provider outcome là `non_capacity_failure`, Query quality chưa được evaluate. Không tạo declaration, trace hoặc formal pair. Disposition SHA-256 `ba3b3e9a4129bbcd8385619240a1eae6f46c6e967bffbcffcfc38eadf5de1f9b`; authorization/window đã consumed/tombstone, Query tiếp tục OFF và không artifact nào được retry/reuse/carry-forward.
- Sau terminal failure trên, owner chỉ authorize kiểm recovery. Independent health root `provider-health-smoke-e14b02f-20260822T004512Z` trên clean commit `e14b02ff24d7ebe16e05f938f50d79f47101f985` đạt `5/5`, zero retry, một attempt/request, timeout 30 giây; P50 `1.54s`, P95 `16.45s`, provider configuration SHA-256 vẫn là `9d978ec3fb533f7316eb98928ec0f3cbbde9f6e52b33ae3b45aff15d1d61416f`. Artifact SHA-256 `b4a994186b51a85b710780ced854a8fd133b6914dafcb24e73dfe304119e9fe0` chỉ là `health_proof_only`, đã consumed, không đánh giá Query quality và không được reuse/carry-forward vào formal window. Provider đã hồi phục ở thời điểm probe; formal attempt vẫn cần draft, owner authorization, never-used root, preparation, boundary, fresh formal smoke, declaration và traces hoàn toàn mới.
- Owner approve exact draft SHA-256 `8de7011388c01b49e06c652173709c2f687ef83ba45a05c4833e5e87fb5e977a` cho window `query-formal-d728931-20260822-01` trên clean commit `d72893168f160d83b3df1a863af5429438e07dde`. Offline/provider-boundary preflight pass `13/13`, rollback pass và Query flag giữ OFF. Fresh formal smoke duy nhất pass `5/5`, zero retry, một attempt/request, timeout 30 giây; P50 `2301.94 ms`, P95 `3962.53 ms`, smoke SHA-256 `62d9b0a6fa30b46b2c7ba623795b6f32b2d6933f3c02655da3b74195e00e0f` và provider configuration không đổi.
- Window `d728931` vẫn terminal trước declaration vì operator đã stringify `completed_at` mà `ConvertFrom-Json` materialize thành `DateTime`, rồi parse chuỗi locale bằng invariant `DateTimeOffset.Parse`. Không tạo declaration, trace hoặc formal-pair directory; số formal pair bắt đầu là `0`, không phát thêm provider traffic và Query quality chưa được evaluate. Disposition SHA-256 `763b70369d9f6427ed699d8ddc033d84437a3506de78ffb202e2a5dc48ea2ae0` khóa authorization/root ở consumed+tombstoned; không được sửa lệnh rồi tiếp tục, retry, reuse hoặc carry-forward.
- Smoke-binding hardening test-first tại `e2b072e` thêm helper read-only `resolve_query_formal_smoke_binding.ps1`: kiểm exact contract `5/5`, zero retry, một attempt/request, timeout đúng 30 giây, provider outcome/hash; xử lý `[datetime]` trực tiếp hoặc string invariant round-trip và tính deadline baseline 30 phút. Regression khóa locale `vi-VN`, inconsistent outcome và timeout drift; Query preparation unit file, PowerShell parse, secret scan, diff check và correctness/security review đều pass. Không đổi provider, threshold, oracle, feature state hoặc authorization; formal attempt kế tiếp vẫn cần clean commit/draft/approval/never-used root và toàn bộ artifact mới.
- Owner approve draft SHA-256 `c028f615db52920df2ce34abfa821c6af224867b1bc7c25c12ab98d51a798cba` cho root `query-formal-5a923e5-20260822-01`, nhưng một probe read-only ad hoc trước preparation lại stringify authorization expiry theo locale rồi gọi `DateTimeOffset.Parse`. Contract first-failure làm authorization/root consumed+tombstoned trước offline preparation; provider traffic `0`, không có smoke/declaration/trace/pair và Query tiếp tục OFF. Disposition SHA-256 `0b48ddab8ee0c04139fb323556797e87c3adfa2d2f0673312de9534cbf25686f`. Future attempt phải bắt đầu bằng canonical `prepare_query_formal_window.ps1`, không thêm probe expiry ad hoc, và vẫn cần exact draft/authorization/never-used root hoàn toàn mới; yêu cầu standing approval không thể thay thế binding tới SHA/commit/root/expiry tương lai chưa tồn tại.
- Owner approve draft SHA-256 `7147c2d9fab3c7df62b088ce8413f1c05ab20eaa1483dcecb00b6d0e54b61690` cho root `query-formal-e3e31ca-20260822-01`. Canonical preparation entrypoint được gọi trực tiếp nhưng process chưa bind `QDRANT_COLLECTION` và `RAG_EVAL_EXPECTED_COLLECTION`, nên dừng `query_eval_collection_binding_invalid` trước khi tạo root artifact. Authorization/window đã consumed+tombstoned; provider traffic `0`, không có boundary/smoke/declaration/trace/pair, Query tiếp tục OFF. Disposition SHA-256 `eb938ea85b79d06eea7059f82e562c740b1add252e0461a3c49dd30ca4fa881e`; không được set biến rồi retry cùng authorization/root. Future attempt phải thực thi nguyên block với bốn process binding trước canonical entrypoint, rồi dùng draft/authorization/root/artifact hoàn toàn mới.
- CRAG + Claim Repair vẫn OFF sau diagnostic V3 dừng ở `3/9` case pair vì một provider retry. Recovery signal mới không authorize CRAG và không được carry-forward; mọi CRAG diagnostic/formal window tương lai vẫn cần declaration, smoke và run-root riêng.

## Checkpoint thực thi — 2026-08-14 (lịch sử, superseded bởi checkpoint 2026-08-22)

- Operator window-06 đã tombstone ở `17` transport completion, chỉ `10` eligible trace và `7` calculation-invalid; `carry_forward_requests=0`.
- Burst `20dfd03b262a2eedf15731d7` đã hoàn tất `100/100` request với 100 unique trace hash và không ambiguous, nhưng canonical burst gate vẫn `rejected` vì `owner_declaration=false` do validator so sánh hai miền `window_sha256` khác nhau. Kết quả chỉ là throughput evidence; không phải pilot hợp lệ theo contract 7 ngày lúc chạy hoặc contract 3 ngày prospective, organic, quality, UI-parity hay default-rollout evidence.
- Root fix cho campaign tương lai nằm ở commit `d77f28c09c995dd983591a730db0e07a79b2f4e2`; fix không sửa artifact lịch sử, không replay traffic và không làm gate gốc pass hồi tố.
- Owner disposition `data/integrated_hardening_v1/evidence/grounded-math-20dfd03b-deferred-disposition.json`, SHA-256 `55007d64095d95005cb696f38c6a00e326624b5c8b024b7db6bf512df910f21e`, tiếp tục khóa burst lịch sử ở `deferred_pending_valid_pilot`. Artifact prospective `data/integrated_hardening_v1/evidence/grounded-math-3d-100-owner-authorization.json` đã mở đúng một pilot mới theo contract 3 ngày/100 request và cho phép bật riêng Grounded Math trong controlled-demo; không sửa disposition/tombstone cũ, không carry-forward và chưa mở owner review, interaction matrix, default ledger/bundle hoặc feature activation ngoài controlled-demo.
- Runtime burst lịch sử đã dừng và không được reuse. Lần launch thử operator window-12 ngày `2026-08-14` thất bại trước dispatch do wrapper thiếu `PYTHONPATH`; tombstone mới ghi `0` completion, `0` eligible và `carry_forward_requests=0`. Không có evidence nào từ window-12 được chuyển tiếp.
- Fresh operator window-13 đang chạy trên serving commit `7b9d57562a669984b843d48d6d7ddf09048c472d`, tooling commit đã review `066bee2bec69a88999ed6b069648d40e8771beb7`, campaign `19aacefbe67b1aa3907a490c`, contract `grounded-math-3d-100-v1`, từ `2026-08-14T10:00:35.8966444+07:00` đến mốc tối thiểu `2026-08-17T10:00:35.8966444+07:00`. Request đầu tiên đã complete; Scheduled Task `ChatBotProject-GroundedMath-Operator-Window13` chạy mỗi 5 phút, `IgnoreNew`, đã pass cả manual start và lần tự kích hoạt đầu tiên mà không dispatch card chưa đến hạn. Trạng thái hiện tại là collecting `1/100`, không có stop marker; default rollout tiếp tục `all_off` và release ledger vẫn `incomplete`.

## Checkpoint thực thi — 2026-08-10 (lịch sử, superseded bởi checkpoint 2026-08-14)

- Phase 0 và Phase 1 đã hoàn tất. Runtime pilot/control hiện bind exact commit `7b9d57562a669984b843d48d6d7ddf09048c472d`; pilot chỉ bật Grounded Math, control giữ `all_off`.
- Grounded Math đã đạt ba current-commit formal pair, series guardrail `production_eligible=true`, formal review 16/16 và rollback/restore reconciliation trên detached checkout sạch.
- Single-owner governance đã được `bao.nguyen` ký cho `scope=controlled_demo`, `risk_accepted=true` và đủ ba signoff `rag`, `security_qa`, `operations`. Quyền này không áp dụng cho default rollout.
- Proof 5/5 và owner declaration cho full campaign đã hoàn tất. Window dừng ở `30/100` đã được tombstone, không carry-forward request hoặc downtime. Window thay thế sạch bắt đầu `2026-08-10T04:33:45.6530163Z`, mốc tối thiểu `2026-08-17T04:33:45.6530163Z` và bắt đầu lại ở `0/100` eligible calculation request.
- Owner-authorized burst 100 request, nếu được chạy, chỉ tạo throughput/safety evidence với `count_toward_pilot=false` và `qualifies_as_7_day_pilot=false`; nó không thay thế checklist LAN pilot đang hiệu lực tại thời điểm chạy và cũng không thỏa contract Math 3 ngày/100 prospective ngày `2026-08-14`; default rollout không được authorize.
- Snapshot lịch sử lúc `2026-08-11T07:09:22Z` đã xác nhận runtime identity và app health hợp lệ trên exact commit `7b9d57562a669984b843d48d6d7ddf09048c472d`; app/pilot/control lúc đó listen ở `8180/8200/8210`, pilot chỉ bật Grounded Math trong `controlled_demo`, control giữ `all_off`. Gate false tại `0/100` là trạng thái collecting fail-closed dự kiến; không phát sinh synthetic production traffic. Health SHA-256 `51728bed4f75523f3131a47531822949d328a38fbd1d3c5884f40ec37a995489`, gate SHA-256 `b9ccf3ddc75d97beca0e7fff11c29aad9ac1898e0b56ec1da1f496aaedfaf984`.
- Reviewer contract của pilot được chốt bằng `.local/math-pilot-7b9d575/review-contract-resolution.json`: `bao.nguyen` là human reviewer duy nhất cho đủ 20 case dưới signed `single_owner`; Codex chỉ chuẩn bị metadata và hỗ trợ kỹ thuật, không được tính là independent human reviewer. `pilot-window.json` được giữ nguyên vì đã bind SHA.
- Query Decomposition đã có telemetry metadata-only tách planner, từng retrieval/correction branch, final context và final generation; rollup cost được đánh dấu để không cộng hai lần. Diagnostic không-formal sạch gần nhất tại `reports/decomposition/20260808-diagnostic-7b9d575-dirty-02/diagnostic.json` chạy đủ 13+13 case, không có provider failure nhưng không đạt: cost ratio `1.899837 > 1.35`, pass tổng `2/13 → 7/13`, decomposition `7/10`, branch accuracy `86.67%`, citation accuracy `70%`. Root fix đã chặn trước final generation đối với câu hỏi high-risk có partial coverage do nhánh `grounded_negative`; evaluation subprocess hiện được cấp `all_external` trong đúng `evaluation` scope nên không còn local `ExternalProcessingDenied`. Diagnostic `-04` đã tới ProxyLLM nhưng bị dừng sau `19/19` generation call trả HTTP 503 `no_capacity` trên 9 baseline case; chưa chạy candidate và không đánh giá cost/quality. Vì vậy chưa có post-fix cost ratio hợp lệ, chưa mở formal window và Query vẫn OFF; `-01`, `-03` và `-04` đều được giữ làm tombstone.
- Query WIP đã qua review Standards/Spec, security boundary review và được freeze tại commit `c123c399817236b63e8dcfc57ff608feb2853ff1`. Diagnostic runner fail-closed nếu commit/manifest/worktree/runner/fixture drift hoặc baseline gặp provider outage; default rollout và Query vẫn OFF.
- Checkpoint Query `2026-08-10`: diagnostic đầu trên `498257f` thiếu `RUN_DECOMPOSITION_EVAL_FIXTURE=1` nên chỉ giữ declaration làm tombstone; diagnostic kế tiếp chạy đủ hai arm nhưng dừng vì `baseline decomposition cost does not reconcile`. Root cause là evaluator ưu tiên top-level `decomposition_usage` được snapshot trước stream thay vì bản current trong `debug.generation_metrics`; root fix tối thiểu đổi precedence, có regression test, đã qua Standards/Spec/security review và được freeze tại `a106befec6fc5dfbf29ad34cf8e89a36952ff1e5`. Relevant Query/CRAG suite pass; full unit suite chỉ còn failure strict-stream đã tồn tại ngoài scope.
- Diagnostic sạch `reports/decomposition/20260810-diagnostic-a106bef-01/diagnostic.json` trên exact commit `a106bef` đã pass mục tiêu hỗ trợ: cost ratio `1.319309 <= 1.35`, baseline/candidate cost `0.0196675 → 0.0259475`, final-generation call giữ `9 → 9`, cả hai arm `cost_reconciled=true`, provider failure bằng `0`; pass tổng `3/13 → 5/13`, decomposition `6/10`, branch accuracy `86.67%`, citation accuracy `65%`, budget violation và simple planner call bằng `0`. Diagnostic SHA-256 `2b10a3703bf9543c9e48ec5eecdc42b7c060eca3a07e11e0b6b5810801c090e0`; declaration SHA-256 `5a58743719d3c4e4c7213d68463c387c238d1e8fcf57f1ddf58d44f2901798fe`.
- Query formal pair 01 trên detached checkout sạch `a106bef` có rollback `28/28` và provider smoke `5/5`, `0` retry, nhưng fail đúng hai check `branch_accuracy_complete` và `branch_citations_complete`; mọi provenance/safety/budget/latency/cost check khác pass. Candidate đạt `5/13`, decomposition `6/10`, branch accuracy `86.67%`, citation accuracy `65%`; latency ratio `1.115647`, cost ratio `1.251994`. Run SHA-256 `96f5db971489b4065a5a448655ea52c8ce0b2a0f3aad5eb0cc3a5651f4bba529`, gate SHA-256 `d9fb97ab4aa0ef6cd1ab3faaaed92a68bbdfb6765031c48742876a22482a42d4`; pair 02/03 không chạy để tránh chọn rerun đẹp.
- Owner `bao.nguyen` đã duyệt phương án khuyến nghị cho Query manifest. Canonical generator nay freeze hai scope: Query-only `13` case (`10` complex + `3` simple), SHA-256 `6976cbbe4c9500b7c0755c5944775e326106a780bb2910bfa71167787a1d0bf8`, không còn phụ thuộc Grounded Math; và Math+Query interaction `3` BOM case, SHA-256 `d21495e86faca22c455f745a7b9fd7f249643f31e76f36e086f8af4467b0932b`, giữ nguyên nhãn `full_answer` + phép `sum`. Branch contract tách exact retrieval provenance (`expected_citations`) khỏi exact rendered source (`expected_rendered_citations`). Retrieval so theo tập canonical source identity duy nhất: duplicate chunk cùng nguồn/trang được gộp, còn source identity khác expectation bị từ chối. Bốn Query-only high-risk case khóa overall `insufficient_evidence`, exact branch outcomes, terminal factual-claim count và rendered-source count đều bằng `0`, nhưng vẫn kiểm exact source đã retrieve; gate formal bắt buộc tổng terminal violation bằng `0`. Preflight từ chối scope thiếu/trộn, case ID interaction sai, Math contamination hoặc contract drift. Formal Query series mới chỉ được chạy sau freeze commit sạch; Query vẫn OFF trong lúc checkpoint này được commit.
- Formal pair 01 trên exact commit `5557715cdec1e8f78e22bd5d8a08e52de80b8632` được giữ làm tombstone, không chọn rerun đẹp: `branch_accuracy_complete` đã pass `1.0`, provider failure/retry bằng `0`, latency/cost/budget/safety đều pass, nhưng `branch_citations_complete` còn `0.783333` và complex pass giữ `4/10 → 4/10`. Run SHA-256 `c148f80435205c072c8d06427ae9c903813d80d076b69498653b160660028823`, gate SHA-256 `245badb65d9f2640139ac5a9608f33ca74370cebfa75b10d1904d525d0b7baf5`; pair 02/03 không chạy. Root cause là evaluator dùng cùng `expected_citations` cho source đã retrieve và source đã render, khiến terminal refusal không thể biểu diễn “retrieval đúng nhưng render rỗng”; root fix TDD thêm hai exact contract độc lập, không hạ gate hoặc nới citation oracle.
- Formal pair kế tiếp trên exact commit `993c1a825d8e4fb103977d10e9dd9788bd1f0787` cũng được giữ làm tombstone: mọi check ngoài `branch_citations_complete` đều pass, complex `0.4 → 0.5`, branch accuracy `1.0`, citation accuracy `0.866667`, terminal violation `0`, provider failure/retry `0`; pair 02/03 không chạy. Run SHA-256 `37f92b2a22a9025abc1daf43c896e8b41918a7f0ed3bd5aac9c9ce46006f90d8`, gate SHA-256 `669fea996b643f20c15c54bad2a24abd9b9fc8014301f0683010b6b4c9df0ac2`, pair SHA-256 `7bcbb2dbd3eb03098c5c8b958384a04465f7ecedb1b16ad024671f6c3e866795`. Diagnostic evaluation-only xác nhận nhánh alias chỉ phục vụ top-1 nhưng branch diagnostics công bố toàn bộ raw retrieval, làm source của nhánh khác bị cross-attribute.
- Root fix TDD tại `7b2d3842981931d97c788de7c74749a004784850` khóa policy, diagnostics, citation và final context trên cùng served-evidence boundary; general branch chỉ phục vụ top-1, correction hợp lệ được ưu tiên trước original docs, còn raw initial retrieval count/token chỉ ở telemetry. Relevant suite pass `187` test; full unit suite chỉ còn failure strict-stream đã tồn tại ngoài scope. Detached rollback pass `33/33`; Query-only preflight `13/13` và Math+Query preflight `3/3` cùng fingerprint `b4066ab6ce9005715192d312c4ffa10b73512a027c5cf88858dea6bf71d1f90e`. Formal pair 01 mới vẫn được tombstone RED: complex `0.4 → 0.7` và candidate tăng từ `0.5` ở pair trước lên `0.7`, branch accuracy `1.0`, citation accuracy `0.95`, terminal violation `0`, nhưng fail `provider_retries_not_increased` và `branch_citations_complete`; ba generation retry đều là ProxyLLM `RuntimeError`, case citation còn lại kết thúc bằng safety post-check refusal nên không render SourceID. Run SHA-256 `a2413af15d3833afbeaa5a6263d613447d0279a5c4a9fa90cf712d5bc157457f`, gate SHA-256 `13b5d8762b79f88fed54a05cc66723e95721ff56457475dcf1ee115703267a7d`, pair SHA-256 `8e238928b054a6b458874b1434341714d06f69f4a3b7220d227a0a52f18d27cd`; pair 02/03 không chạy. Recovery capacity smoke kế tiếp vẫn fail `4/5` vì một `APITimeoutError`, SHA-256 `f5d20e4ce0ec625389a13b1610d43fe8715cae8e868a90f80708c85939d381aa`, nên chưa mở series mới và Query tiếp tục OFF.
- Fresh provider smoke `provider-smoke-05.json` sau đó pass `5/5`, `0` retry, SHA-256 `d07a1c3e087b4528f97753d1756431f3140323fb6a473dd4e8fcdd5f55f7d49f`, nên formal-series-02 được mở đúng từ pair 01. Pair này vẫn phải tombstone RED vì baseline có `1` provider failure và `3` retry; candidate có `0` provider failure/retry, decomposition `10/10`, branch accuracy `1.0`, citation accuracy `1.0`, terminal violation `0`, complex `0.3 → 0.6`, simple cùng `0.666667`, cost `0.0188025 → 0.0262775`, P95 `27860.17 → 24770.84` ms. Check fail duy nhất là `baseline_provider_failures_zero`; run SHA-256 `92a6d3497e5ab1238adfaabd6bc19b34b648a1248ca8e8bbaa15ff00824fe4fa`, gate SHA-256 `a7fb914546224d0c777562831ea9bfcca4dcd1b255da7af63c4aad6a93d6c508`, pair SHA-256 `7e4a3f9e29d5660910cc1cc68767dcb094b0c73732ae920e11d839a54452c515`. Pair 02/03 không chạy; Query vẫn OFF và series tương lai cần declaration + smoke mới.
- Graph Retrieval current-commit cycle đã bắt đầu offline từ base `c123c39`. Fixture hiện có `17` case, trong đó đúng `10` relational case thuộc Technical/Production/Maintenance và bao phủ `HAS_VERSION`, `SUPERSEDES`, `CONTAINS_PART`, `USES_MATERIAL`, `APPLIES_TO`; manifest SHA-256 là `def156e8d30a9184fd7105ca5e799ddef311c98a5c88c7bc59001ad42ee8a136`. Claim oracle đã lên `deterministic-labeled-claims-v2`: mọi positive relational claim bắt buộc predicate + target trong cùng một mệnh đề khẳng định; polarity `không/chưa/chẳng/chả` chỉ chặn mệnh đề chứa relation đó, nên câu phủ định không pass giả và mệnh đề phụ phủ định relation khác không bị loại oan.
- Current fixture preflight chỉ đọc đã pass `17/17`, `0` failure, `21` approved edge, structured coverage/provenance đều `1.0`, pending serving edge bằng `0`; fingerprint `71edefd6023e72fdaee5acceac6a18a5e9dbb8a30a1f3a10036da05f49e178cf`. Queue mới `.local/graph-cycle-c123c39/review-queue.jsonl` có SHA-256 `a5cf221e549a37821deba9ec891b49e4822340352a8525f8831397221f9bdc2f`; semantic projection khác queue 21-edge cũ, nên không carry-forward review labels. Cần review queue mới trước formal graph-only pair; Graph vẫn OFF và chưa có provider/formal run.
- Checkpoint Graph `2026-08-10`: queue current-contract đã được review lại `21/21` edge bởi hai reviewer `bao.nguyen` và `tran.nghi`, precision `1.0`, SHA-256 `4a02676d496b216a7dfe94c588965aab00ab9ba991fc5d17e5cb9edc7c2a64b1`. Audit fail-closed đã sửa runner để baseline giữ `all_off`, candidate chỉ bật Graph trong `evaluation/all_external`; gate bắt buộc thêm non-relational no-decrease và cost `<=1.5`; preflight không còn chấp nhận Graph artifact chỉ `passed=true` khi chưa đủ production eligibility. Cycle đầu trên `262da71` đã pass nhưng bị vô hiệu hóa cho activation sau khi audit phát hiện bundle không thể biểu diễn đúng independent review và single-pair artifact có thể bị dùng thay series. Root fix TDD tại `4bc666c11b81e28cfcc83d81d7128522736accfe` buộc Graph controlled-demo decision dùng series được recompute từ ba pair hash-bound, bắt buộc đúng `multi_reviewer/independent`, từ chối exception `single_owner`, và vẫn giữ default rollout cần owner signature riêng.
- Trên detached checkout sạch của `4bc666c`, preflight pass với fingerprint `71edefd6023e72fdaee5acceac6a18a5e9dbb8a30a1f3a10036da05f49e178cf`, rollback Graph-only pass `70/70` test và ba provider smoke dùng cho formal evidence đều pass `5/5`, `0` retry. Ba matched pair `formal-pair-01..03` đều pass; baseline/candidate cùng `10/17`, relation accuracy ổn định `0 → 9/17` (`+52.94` điểm phần trăm), wrong-answer `7 → 7`; latency ratio lần lượt `1.020415`, `0.539582`, `0.978831`; cost ratio `1.371443`, `1.335126`, `1.342708`; mọi RBAC/provenance/review/pending/budget check đều pass. Series guardrail SHA-256 `8701c693cff4994b676d4f7a6281a56692e4b75f80c36e2672e1e67eb393264d` pass `9/9` check, bind `multi_reviewer/independent` và recompute validator trả `true`. Đây mới là technical eligibility để owner xét mở pilot, chưa phải pilot/live authorization: chưa tạo accepted controlled-demo decision hoặc feature-on bundle, Graph vẫn OFF, chưa có LAN pilot 7 ngày/100 routed relational request, interaction matrix, technical acceptance và owner release decision.
- Theo authorization tạo target nhưng chưa bật feature, restore drill đã tạo SQL database `Mech_Chatbot_DB_RestoreTest_RAGPilot_20260810_4bc666c` và Qdrant collection `TaiLieuKyThuat_v2_RestoreTest_RAGPilot_20260810_4bc666c` với `231/231` point; verifier exact `4bc666c` pass trên restore evidence SHA-256 `1f69e8bcdd87147f9d526bd0b3cf0738ad16c9576d4d10e2396b6258d5d7af27`. Owner `bao.nguyen` đã chấp nhận exact snapshot fingerprint `b1a73fa7d90500e5c738154e30b64ade68a0b59e5de7066f0e7981e613209b11`; Graph controlled-demo decision SHA-256 `28d02f7f384d02027e048718f94b8b82fe4071fdd83e662b5d8a707f3b5f24e5`, decision ledger SHA-256 `9a0e6ee497636fe80045d265a2bb3cfb8b98572a6da2b43c9e99f6c45eea0f5c`, activation bundle SHA-256 `0fc9f91920da899f802c2a0777c10e34a764a1427011080d8f2de28d282b06bd`. Quyền này chỉ áp dụng cho Graph `controlled_demo`; default rollout vẫn OFF.
- Graph control/candidate runtime đã từng start trên detached checkout sạch `4bc666c` tại `8103/8104`. Health có xác thực xác nhận control `all_off`, candidate chỉ bật `RAG_GRAPH_RETRIEVAL_ENABLED`, cùng exact SQL/Qdrant target, fingerprint, bundle và restore evidence SHA; runtime identity lần lượt `0a47442886d5d2dbba8b5ea5cd2c48dbd414a3518937acd130dea35c51ab9526` và `ff4596e224804fbae3bc17dc938b3069fda92a822339dcd56dbb57108973fefc`. Runtime receipt `.local/graph-cycle-4bc666c/graph-controlled-demo-runtime-receipt.json` SHA-256 `82e2a1967d1f12b65e0f283100305fb0d2b5e70a4d039af9324b6aec1a3dce18` pass toàn bộ check. Đây là historical receipt: cặp runtime đã được dừng fail-closed khi source bắt đầu thay đổi; không có request hoặc thời gian nào được carry-forward vào pilot mới.
- Owner `bao.nguyen` đã chấp nhận phương án governance riêng cho Graph controlled-demo pilot theo `single_owner`: review phân tầng tối thiểu `20` case, mọi failure/refusal/access-denied bất thường/low-confidence case bắt buộc owner review; Codex chỉ chuẩn bị metadata và hỗ trợ kỹ thuật. Artifact vẫn phải bind lại đúng final RC trước pilot. Exception này không thay đổi formal Graph gate: formal review vẫn bắt buộc `multi_reviewer/independent`, tối thiểu hai reviewer; default rollout vẫn OFF.
- Root fix telemetry/gate Graph đã land tại implementation commit `e165e5cc20d549ec73a3844fe438e899c9ea60c7`: mỗi request Graph phát metadata-only evidence cho runtime identity, security, citation/provenance, budget/provider/leakage, low-confidence và deterministic refusal; owner review dùng trace ID đã hash và exact case set; gate fail-closed khi thiếu review, Graph error, raw field, runtime/provider/budget drift hoặc refusal không khớp template/rag_end. SQL `Confidence` được chuẩn hóa sang bounded `float` trước JSON diagnostics. Regression liên quan pass `426` test; coverage ba module authority lần lượt `83%`, `96%`, `91%` (tổng `87%`); dedicated dependency audit trả `No known vulnerabilities found` với lock SHA-256 `5f08a35f8ffa56f2b2d295628b6a1eccda4d36059c68de4c43733842c42ff197`.
- Vì telemetry/runtime contract đã thay đổi sau `4bc666c`, mọi Graph decision/bundle/runtime/formal artifact cũ chỉ còn là lịch sử và không được authorize source mới. Final Graph RC sạch hiện là `5732c421f227052ed659915f66270fea9150fed8`; governance pilot `single_owner` 20 case đã bind đúng commit, SHA-256 `c7661076abe3e7d84cb37f69306da815aa19df0230d0fb2eb249a9e11073b1b5`, trong khi formal vẫn giữ `multi_reviewer/independent`. Read-only restore reconciliation pass trên disposable SQL/Qdrant target cũ với `231/231` point, receipt SHA-256 `a35ab5184d7503a4086f65b24f7f6fe473f37e04cf5b7b41786f49dcf68c6762`; preflight SHA-256 `51edafc0e8dc35b9f2a3ad869849e9ece935ec5940c7491655ac85ccb9a8d1af` pass `17/17`, fingerprint giữ `71edefd6023e72fdaee5acceac6a18a5e9dbb8a30a1f3a10036da05f49e178cf`; rollback SHA-256 `8b52463ad87e91178102aecf7703d631f672c577f2bf3e68a5cd7863bf943cc4` pass và Graph flag vẫn OFF.
- Graph formal-series attempt trên `5732c42` đã dừng fail-closed, không tạo series. Provider smoke đầu tiên bị local `internal_only` chặn trước egress và được giữ làm tombstone; smoke evaluation-process-only kế tiếp pass `5/5`, `0` retry. Pair 01 đầu tiên RED vì thiếu binding tới review queue và được giữ nguyên; attempt mới với exact queue pass, pair 02 pass, nhưng pair 03 fail duy nhất `latency_within_budget`: baseline/candidate p95 `8681.82 → 21004.82` ms, ratio `2.419403 > 1.5`, trong khi mọi quality, RBAC, leakage, provider-error, cost, provenance và review gate đều pass. Cost ratio pair 03 là `1.348`; pair 01/02 latency ratio lần lượt `1.2629` và `0.9363`.
- Replay artifact xác định `graph-contains-part` có generation latency `3659 → 19041` ms và provider chiếm `97.84%` total-latency delta; cùng candidate case/`1248` input token ở hai pair trước chỉ mất `3224` và `4800` ms. Candidate median generation trên 33 call gần bằng baseline (`3978` so với `3960` ms), correlation input-token/generation-latency là `-0.028`; chưa chứng minh được code-controlled defect. Tombstone SHA-256 `bfde6c28b59c997dd9074b7d8f2fb4c49a41efc993d3369c65594f0a8b65806d` cấm carry-forward pair, chọn rerun đẹp, nới latency oracle, tạo controlled-demo decision/bundle/runtime hoặc mở pilot. Graph vẫn OFF; formal window mới chỉ được mở sau explicit adjudicated design delta và declaration mới.
- Graph supporting diagnostic đã được TDD thành runner riêng, không mint formal evidence: implementation commit `1318781cf60c050a621e6915cba4ca64d31b589a`, trace-binding root fix `12caa366cf4e1dd4f47a85481bc5a9047dacf01b`, và fail-closed trace-error hardening `3d541abd7222c2cb7b88d4201ee1e53fe8c831b9`. Runner khóa hai pair đảo arm `candidate-first → baseline-first`, bắt buộc trace đủ đúng `17` query/arm, zero parse error, `multi_reviewer/independent`, latency p95 target `<=1.25`, cost `<=1.5`, và luôn ghi `formal_window_authorized=false`, `feature_enablement_authorized=false`. Focused suite trên exact `3d541ab` pass `86/86`; module diagnostic coverage `82%`.
- Diagnostic window trên clean RC `12caa36` có preflight SHA-256 `a997046942cd8e47a7828af1e484f20db0aab39192a97e9885bd68e163453692` pass `17/17`, fingerprint `71edefd6023e72fdaee5acceac6a18a5e9dbb8a30a1f3a10036da05f49e178cf`, `21` edge/`2` independent reviewer, rollback pass và provider smoke SHA-256 `49a4e9997529386c2f23274f38031795b36518932a5b4f93b20d4c1957dd90b9` pass `5/5`, `0` failure/retry. Outcome SHA-256 `d73fe94d3c8d6a4859374220613ad8688319e14185a928a2e058467b020b761f` là `inconclusive`: p95 ratio đổi `2.189261` ở candidate-first thành `0.884741` ở baseline-first, cost ratio `1.342831/1.354523`, dominant stage đổi `retrieval → generation`, pair gate lần lượt RED duy nhất `latency_within_budget` rồi GREEN. Pair-01 candidate có một Qdrant transport fallback và retrieval p95 `13541` ms so với baseline `3245` ms; replay privacy-safe trên exact `3d541ab` bắt đúng `error_event_count=1`, `hybrid_fallback=1`, SHA-256 `e7649ff889461b1da9aaf276804fea92fb7545cf62075ba1f19cb0a372fcf8a7`. Adjudication SHA-256 `7146898fac7e608f5223a4ac2ea87d8baa71313c44a15717777f6f05b7bf973a` cấm carry-forward, same-design rerun, formal/pilot/feature enablement; Graph vẫn OFF. Lượt tiếp theo chỉ được mở bằng materially different owner-adjudicated latency design delta và declaration mới, không dùng lại window này.
- Checkpoint Graph `2026-08-11`: owner đã duyệt thiết kế latency V2 khác biệt gồm `17` cặp case kề nhau, đảo arm theo từng case, Qdrant read-only health probe trước mỗi cặp, tách retrieval/generation latency và dừng toàn cửa sổ khi có error/fallback/retry. Runner supporting-diagnostic-only land tại `e4b5c2c5fd778a65c3157d31869033aa16e9ffda`; root fix tại `9b2a39820e7e3aa3d718c66f37b31122523fa482` bind từng arm vào exact per-case fixture fingerprint và loại đúng các Community marker benign khỏi fallback mà không che lỗi thật. Relevant suite pass `183/183`; correctness/security review không còn blocker. Dependency audit toàn `chat_env` vẫn RED vì `14` advisory tồn tại sẵn trong `5` package, không có dependency mới từ thay đổi này.
- Attempt đầu của V2 được giữ làm tombstone: trên `e4b5c2c`, full-manifest fingerprint bị so sai với singleton case fingerprint; trên `9b2a398`, một foreground process bị local orchestration timeout trước khi có trace. Hai attempt này không phải latency evidence, không được carry-forward hoặc overwrite; fix chỉ thay binding/trace classification và không nới quality, latency hay provider oracle.
- Cửa sổ mới `.local/graph-diagnostic-v2-9b2a398-window-02` có preflight SHA-256 `482398798445a46d9adabb58a20ec25b3a7c31c2e00aaa9a9134fdf4dcff87af` pass `17/17`, đủ `17` per-case fingerprint, `21` edge/`2` independent reviewer; provider smoke SHA-256 `e89df1e686fa8a8e997ebbf3ff923d6daf995af3a4dee26ebb69db3e67f83d93` pass `5/5`, `0` failure/retry. Declaration SHA-256 `7bda26d0561c730155262c58ef06d87555eab149c8b0f6e1ac017192f13c7a8b`; outcome SHA-256 `4ef079d375910aa17fb9c4aff5b606a743dfdbe5b79c2532d1be56c136e4bb75` là `inconclusive` và dừng đúng fail-closed sau `4/17` cặp: Qdrant health đều pass, không execution failure/retry, nhưng candidate của `graph-uses-material` có một event `bm25_retrieval` vừa được phân loại error vừa fallback. Vì cùng một event được cộng ở hai nhóm nên aggregate `provider_failure_count=2`, không phải hai anomaly độc lập. Cửa sổ chưa đủ stage/arm-order/quality evidence, mọi latency/cost ratio quyết định giữ `null`, `formal_window_authorized=false`, `feature_enablement_authorized=false`; không mở formal series hoặc pilot, Graph vẫn OFF. Bước gated tiếp theo là xác nhận bên ngoài rằng retrieval/provider đã hồi phục rồi tạo declaration/window hoàn toàn mới; không chạy tiếp window này hoặc carry-forward bốn cặp đã xong.
- Recovery cho window kế tiếp được xác nhận bằng exact preflight `17/17`, sparse/BM25 probe `5/5` ở timeout `3` giây và provider smoke `5/5`, `0` retry. Diagnostic trên `9b2a398` chạy sạch `10` cặp đầu nhưng dừng ở case 11 dù candidate `graph-site-denied` đã pass `1/1`, correct `access_denied`, provider/error/fallback/retry đều `0`: terminal exact-code miss không phát aggregate `retrieval`, làm latency validator thiếu sample. Outcome SHA-256 `5ada86e76d22862a842e59e25086d8887c7f6c4a02bd66bc09b21431c31e7d6f` được giữ làm tombstone; không carry-forward `11` cặp. Root fix TDD tại `fe57f7d5585021380832df6966d662230b57bd8e` phát retrieval metadata trước terminal `rag_end` cho cả `access_denied` và `no_docs_for_exact_code`, không đổi RBAC/refusal hay nới latency oracle; relevant suite pass `143/143`, correctness/spec/security review không có finding.
- Clean RC `fe57f7d` có preflight SHA-256 `482398798445a46d9adabb58a20ec25b3a7c31c2e00aaa9a9134fdf4dcff87af`, provider smoke SHA-256 `fd61afef49b07b9647a624ef732da51f1f58905a5290274431468514c90e3732` pass `5/5`, `0` failure/retry. Declaration SHA-256 `0264d7ba62481113938dfbb75e9f3c5fab749d3371807f17fdd9e01bf0863365`; diagnostic chạy đủ `17/17`, mọi health pass, `0` execution/provider/error/fallback/retry, arm-order consistent với median ratio `1.009648` baseline-first và `1.085429` candidate-first, spread `0.075781`; cost ratio `1.349857 <= 1.5`. Outcome SHA-256 `e71541fff80b06b1d82faa9924a9648b9a95f00967161f467585c98b245a0519` vẫn `failed`: latency p95 `8035.55 → 17892.32` ms, ratio `2.226645 > 1.25`; retrieval p95 `2400 → 5000` ms, ratio `2.083333`, generation ratio `1.179764`; candidate `10/17`, baseline `9/17`, full quality gate không chạy vì đây chỉ là supporting diagnostic. `formal_window_authorized=false`, `feature_enablement_authorized=false`; Graph vẫn OFF.
- Tail diagnosis không cho thấy Graph traversal là phần chi phối riêng: case đầu `graph-family-version` candidate-first có total `17824` so với baseline `5992` ms; Qdrant dense/BM25 `1997/2615` so với `581/910` ms, Jina rerank `6115` so với `577` ms, generation `5250` so với `3049` ms, còn Graph traversal chỉ `333` ms. Với `17` mẫu, nearest-rank p95 là giá trị max nên một tail đồng thời qua nhiều stage/provider ở case đầu quyết định gate; cold-start chỉ là giả thuyết cần thiết kế mới kiểm chứng. Không được bỏ case đầu, đổi percentile, carry-forward hay same-design rerun. Bước gated tiếp theo cần owner adjudicate một design delta mới có symmetric unmeasured warm-up cho cả hai arm trước 17 measured pair; nếu không chấp nhận warm-up thì chuyển feasibility review thay vì mở formal.
- Warm-state diagnostic V3 được freeze tại `32fc8d739a635ff17fd4606e5a32df91b5f4a96a`: hai warm-up pair cố định trên `graph-uses-material`, thứ tự `baseline-first` rồi `candidate-first`, trace riêng và không tính vào measured latency/cost/quality. Full 17-case warm-up bị loại trước implementation vì có nguy cơ vượt provider-smoke freshness `30` phút. Public CLI RED/GREEN, relevant suite pass `146/146`, touched-module coverage `81%`; correctness/spec review không có finding, security review phát hiện rồi xác nhận đã đóng lỗi trace snapshot lệch path. Dependency audit vẫn RED `14` advisory tồn tại sẵn trong `5` package, không có dependency mới.
- V3 window-01 có preflight SHA-256 `482398798445a46d9adabb58a20ec25b3a7c31c2e00aaa9a9134fdf4dcff87af` pass `17/17` và smoke evaluation-only SHA-256 `a518f42dae24515533a7db795565a01e3097247a645ec78f83225ebc406de172` pass `5/5`, `0` retry. Window dừng đúng fail-closed ở warm-up pair 1: candidate strict BM25 mất `3236 ms`, trả `ResponseHandlingException`, broad BM25 ngay sau pass `227 ms`; measured giữ `0/17`. Declaration SHA-256 `7aff6ed38551429ba67af5c63ce0f0606d2b9fcc0a0a391c577edf0a786c18f3`, outcome SHA-256 `7a2629ce0652cabe2571a3d5094f57e620d1346bf26177fbe7cce3cbac14d774`; không carry-forward.
- Exact strict-filter sparse recovery probe SHA-256 `1a727262f6fdfcd35d1f713e2e316321f2ae76ae539b9b61be47292b27ed9e2d` pass `5/5`, lượt đầu `850.549 ms`, bốn lượt sau `234.681–239.918 ms`, nên loại lỗi request/filter deterministic nhưng không chứng minh full-window stability. V3 window-02 dùng preflight cùng SHA và smoke mới SHA-256 `7de3fc715dfec2d5e83d73599d55c8601e0ba8111f13c3dae7b41a3d05a14a58` pass `5/5`, `0` retry; warm-up mirrored `2/2` sạch. Case 1 sạch về health/provider/fallback, baseline/candidate `10624.14/9509.51 ms`; case 2 baseline lại trả `ResponseHandlingException`, chuyển `hybrid_fallback`, retrieval `8615 ms`, total `18759.45 ms`, candidate không chạy. Window dừng ở `2/17`; declaration SHA-256 `726d1f40c73f9de5e84788f2b0fdb81b799fdebb3b08d020b2e231625d0df8a5`, outcome SHA-256 `f26cf9c60c81202ff65945e1e9b4210baae5772f9b3f3ab6ec1201bd78d8f831`; không có p95/cost/arm-order conclusion và không carry-forward.
- Feasibility report `docs/graph-retrieval-feasibility-report-32fc8d7.md` kết luận current evidence không còn phù hợp với cold-start đơn lẻ: retrieval tail xuất hiện ở cả candidate lẫn baseline, trước Graph traversal, và tái xuất hiện sau warm-up/recovery. Current evidence chưa chỉ ra code-local fix an toàn mà không tăng timeout, cho retry/fallback hoặc thay oracle/case/percentile. `bao.nguyen` xác nhận là technical reviewer hợp lệ thay `tran.nghi` cho disposition này và đồng thời chấp nhận với vai trò owner lúc `2026-08-11T05:55:25.4953010Z`; artifact `data/integrated_hardening_v1/evidence/graph-retrieval-32fc8d7-feasibility-disposition.json` khóa `keep_off_technical_limit`. Graph/formal/pilot/default tiếp tục OFF và cấm thêm same-design rerun.
- Checkpoint `2026-08-10`: CRAG + Claim Repair interleaved diagnostic runner đã được freeze tại commit `0b98e12a75123e3fbb07c8bd5ade8867ad31d0f3`. Runner chỉ chấp nhận canonical manifest SHA-256 `beac3aac28b59ac57930b2c7099997efa7bdfda2a76bf65e3f1620d4b0fb897b`, kiểm input drift trước/sau egress, giữ `formal_evidence=false` và fail-closed với provider retry/error hoặc arm-order variance. Current fixture preflight pass `9/9`; provider smoke mới pass `5/5`, `0` retry.
- Diagnostic `.local/crag-cycle-0b98e12/diagnostic-01/outcome.json` chạy đủ hai pair đảo thứ tự arm nhưng kết luận `inconclusive`: pair 01 pass, pair 02 chỉ fail `latency_within_budget`; `0` provider failure/retry, candidate `9/9` ở cả hai pair, cost ratio gộp `1.000690`, latency ratio gộp `1.019142`. P95 ratio đổi `1.235804 → 1.370565` và dominant overhead đổi `correction → generation`, nên không được chọn pair đẹp hoặc mở formal window. CRAG và Claim Repair vẫn OFF; bước kế tiếp là một declaration mới trong capacity window khác, không sửa code từ lượt này.
- CRAG capacity window mới trên detached checkout sạch `a106bef` giữ manifest SHA-256 `beac3aac28b59ac57930b2c7099997efa7bdfda2a76bf65e3f1620d4b0fb897b`; fixture preflight pass `9/9`, fingerprint `9592deb0e747ac9a14d42bf3fe14471baa3b145ad752bdd53fdc491dde3dea7f`, provider smoke pass `5/5`, `0` retry. Interleaved diagnostic chạy đủ hai pair, candidate đều `9/9`, không provider failure/retry, nhưng vẫn `inconclusive`: p50 ratio ổn định `1.138060` và `1.131777`, cost ratio `1.001381` và `1.017774`, còn p95 gate đảo `1.750879` ở candidate-first (fail `latency_within_budget`) thành `0.748829` ở baseline-first (pass). Outcome gộp p50 latency `1.135002`, cost `1.009511`, dominant stage cùng là `correction`, nhưng `arm_order_consistent=false`; declaration SHA-256 `8a5ef11a7e5bf10d58e6218b48beaef929c3f8ecb76b01e11b33120d75940bdc`, outcome SHA-256 `5a769d6b85f130b885adb9c420695c8dea75c95ccd6fdc13a1cf2e08b98e7269`. Không mở formal window hoặc sửa code từ tail-latency variance này; CRAG và Claim Repair vẫn OFF.
- CRAG diagnostic V3 materially different được freeze tại `32da8863057c0d501bf0bfa62b5e8f2b8f896761`: hai series chạy từng case kề nhau, đảo arm `candidate-first` rồi `baseline-first`, trace riêng cho từng series/case/arm, aggregate đủ `9` singleton trước khi gọi lại gate không đổi, và luôn giữ mọi authorization bằng `false`. Relevant suite pass `120/120`, touched-module coverage gộp `81%`; Standards/Spec/security review không còn finding. Window đầu trên exact commit này có preflight `9/9`, smoke `5/5`, `0` retry nhưng được giữ làm tombstone trước arm đầu vì trace riêng nằm trong thư mục `candidate`, kích hoạt overwrite guard của evaluator; declaration SHA-256 `8873004446bb837a11a3c0043e31f1b4234a01b875407ef9bff91c2480664175`, outcome SHA-256 `26579eade347a5ea96ce0b938e6257ce0b3eb317fa4f8264b3f2eb7b923e5794`, `execution_failure=RuntimeError`, `arm_run_count=0`.
- Root fix TDD tại `27e480962492ed119d613b26e7d6e25483ac4fdd` chuyển trace sang sibling `case/rag-traces/{arm}.jsonl`, không tạo trước thư mục output của evaluator; relevant suite pass lại `120/120`, correctness/spec/security review không có finding. Clean RC window-02 có preflight SHA-256 `5f58cffe22c59fc63b3fb4ee08ea91886f7540d8405084245d8066231af1588f` pass `9/9`, fingerprint `9592deb0e747ac9a14d42bf3fe14471baa3b145ad752bdd53fdc491dde3dea7f`; provider smoke SHA-256 `e82534ea334f53b9d18f7873cb6d7f82162d55d4314b7120abc38850a73ef64d` pass `5/5`, `0` retry. Declaration SHA-256 `58c0954fb9f1267b486c441ddf648edbe97515cbe6d9a33852f35c5c6df9332c`; diagnostic hoàn tất `3` case pair/`8` arm rồi dừng fail-closed ở `series-01/crag-version-citation` khi một ProxyLLM `RuntimeError` tạo `1` retry. Trace ghi `2` error event cho cùng retry episode, `0` fallback; vì vậy aggregate `provider_failure_count=2` là event count, không phải hai sự cố độc lập. Outcome SHA-256 `e3673c57012a343b3ff6b50cff0bc5a5d16cebdd8ed832692d8f7edcf04ffcf4` là `inconclusive`, không công bố latency/cost ratio, không carry-forward ba cặp đã xong, không mở formal/pilot/default và CRAG + Claim Repair vẫn OFF. Bước gated tiếp theo là chờ provider được xác nhận hồi phục bên ngoài rồi tạo declaration, smoke và window hoàn toàn mới.
- Owner-decision packet lịch sử `.local/advanced-rag-owner-decision-packet-20260810.json` ghi `approved_recommended_all_three`, Graph fingerprint acceptance và scoped controlled-demo enablement; packet bind Query formal-series-02 tombstone, Graph decision/ledger/bundle/runtime receipt và Math clean window. Snapshot Math lúc `2026-08-11T07:09:22Z` có runtime/app-health valid, `0/100`, collecting fail-closed; health SHA-256 `51728bed4f75523f3131a47531822949d328a38fbd1d3c5884f40ec37a995489`, gate SHA-256 `b9ccf3ddc75d97beca0e7fff11c29aad9ac1898e0b56ec1da1f496aaedfaf984`. Packet SHA-256 `c347e38bf2d3dae6b941b1801c2c4fc50870ed3b163ad92dc8ed7d86582f880f`; mọi default rollout enablement và git push vẫn bị loại trừ.
- Production preflight đã được harden fail-closed: chỉ `default_rollout` mới có `production_ready=true`; health-only chấp nhận đúng healthy `controlled_demo` nhưng trả `production_ready=false`; missing, `evaluation` hoặc scope lạ đều fail. Điều này không đổi Math controlled-demo authorization và không nới default rollout.
- Read-only audit ngày 2026-08-20 xác nhận Math-only default rollout vẫn đúng signed release `67265a0bd6135f9f205521e99bd51870a955b014`: candidate `8200` healthy với đúng bundle SHA-256 `d2b146bb36ba66e3ec6319391fccf3228776f34287b18a0ff490befd588ba660` và chỉ bật `RAG_GROUNDED_MATH_ENABLED`; control `8210` healthy `all_off`, không bundle; cả hai cùng snapshot `8726999787ec3247d5bdc7f30f0a40c6eea205efa24cc41b9b4a4a2f9423f85a`. Chữ ký Ed25519, ledger SHA-256 `0e41b33f87b0f82be66453f105bd956380cfd67c89927aa9914539dfda971208` và owner-review governance đều validate; Scheduled Task vẫn Disabled và campaign có stop marker sau đúng `100/100` attempt.
- Health-only readiness CLI đã có exact expectation cho Git SHA, profile, deployment ID, bundle và tập feature flag. Metadata smoke mới pass cho cả candidate Math-only và control all-off mà không gọi `/chat` hoặc provider. App/browser gateway không chạy trên `8080/8000`, nên không tự start và chưa tạo browser smoke giả. Rollback receipt cũ chứng minh stop có hiệu lực và same-bundle restart thành công, nhưng vẫn ghi `stopper_clean_completion=false` cùng `operational_follow_up_required=true`; điểm này phải được giữ mở cho lần rollback drill kế tiếp.
- Replay offline tombstone Query/CRAG ngày 2026-08-20 không tạo provider traffic. Query manifest vẫn đúng `10` complex + `3` simple, boundary preflight và rollback bind `58dbb08` hợp lệ, nhưng provider artifact canonical không formal-eligible vì `0/5`, zero retry, `non_capacity_failure`. CRAG recompute từ declaration, frozen manifest và stopped-case metadata khớp toàn bộ stored outcome: `inconclusive`, `3` completed pair, `8` arm run, `2` provider failure, `1` retry và mọi quyền formal/pilot/default/enablement đều false. CRAG rollout runner được harden fail-closed cho fresh run-root/zero-byte trace, untracked worktree drift, abnormal evaluator exit, exact manifest/trace identity và consistent gate `(exit 0, passed true)` hoặc `(exit 1, passed false)`.
- Các attempt/window cũ và provider outage cũ tiếp tục được giữ làm tombstone; không chuyển request hoặc thời gian vào bất kỳ future window nào.
- File tracked `data/integrated_hardening_v1/release_decisions.json` vẫn là ledger lịch sử `incomplete`; default-rollout runtime chỉ tin exact Math-only ledger local đã ký và bundle hash-bound của commit `67265a0`, không suy diễn quyền cho feature khác.

### Grounded Math operator hardening cho window kế tiếp

Window-06 đã dừng fail-closed ở `17` completed transport, `10` eligible và `7` calculation-invalid; Scheduled Task bị disable trước card 018 và không carry-forward request/thời gian. Campaign thay thế phải recapture BOM read-only, chỉ chọn part code dạng mã kỹ thuật số-chấm có đúng một quantity fact trong corpus, cùng tài liệu/cùng unit, và chạy `solve_grounded_calculation` thành `valid` trước khi freeze card; chuỗi nhãn tự do hoặc mang ngữ nghĩa không được vào prompt. Quantity/unit chỉ tồn tại trong bộ nhớ preflight và inventory hash, không đi vào public manifest hoặc prompt. Prompt giữ document anchor chuẩn rút từ filename kỹ thuật đã allowlist, chỉ gồm drawing/version/model để retrieval bám đúng document và không chèn phần filename tự do; runner truyền đúng hai part code từ field riêng `part_ids` đã hash-bind qua `current_part_ids`, không parse code từ toàn prompt. `sum` được machine-bind unavailable vì transport không có explicit document scope; `divide` vẫn unavailable do thiếu dimensionless divisor. Corpus hiện chỉ đủ 4 document có pair hợp lệ nên cap được predeclare là `26` card/document và `7` card/document/operation`; selection bias phải được công bố. Mọi campaign operator mới vẫn fail-closed trước dispatch nếu current release ledger đã đổi hoặc base gate chưa reconcile toàn bộ completed trace trước đó; trong phase collecting, reconciliation yêu cầu trace hash/WAL và 6 quality-safety checks khớp, còn `runtime_identity` chỉ được kỳ vọng pass ở final gate khi đủ `100` eligible. URL chỉ được là HTTP loopback không path/query/userinfo/fragment; WAL dùng terminal timestamp thực; companion gate áp rolling cap `3/30 phút` và `35/24 giờ`. Review giữ signed `single_owner`: `bao.nguyen` gắn đủ 20 primary labels và review mọi failure/low-confidence, Codex chỉ hỗ trợ kỹ thuật. Operator volume không tạo organic, quality, UI-parity hoặc default-rollout claim.

## Tiến độ theo phase

- Phase 0 — hoàn tất governance/selective activation và baseline foundation.
- Phase 1 — hoàn tất disposable target, restore reconciliation, Math-only pilot/control runtime và collector/gate metadata-only.
- Phase 2 — hoàn tất Grounded Math pilot, owner review, interaction matrix và Math-only default rollout:
  - [x] Ba current-commit formal pair và series guardrail.
  - [x] So sánh review contract và xác minh 10 candidate labels lịch sử không đổi; tracked controlled-demo decision vẫn `inconclusive`, không được gọi là accepted.
  - [x] Chuyển web/app sang Math-only RC, xác minh runtime/rollback binding và traffic thật chỉ đếm `grounded_math_generation`.
  - [x] Loại trace/runtime drift cũ, restart pilot/control từ detached checkout sạch và mở collector v3 với exact runtime identity.
  - [x] Đóng v3/v4 làm tombstone, chuyển pilot sang exact target 7 PDF, xác minh health/runtime/fingerprint và mở window mới sạch.
  - [x] Ký proof declaration; chạy request đầu tiên qua production UI và dừng fail-closed khi ProxyLLM trả HTTP 503 `no_capacity`.
  - [x] Xác nhận provider hồi phục bằng một provider smoke riêng, không retry và không tính vào pilot.
  - [x] Proof 5/5 đã pass và owner declaration cho full campaign đã được ký.
  - [x] Tombstone window đã dừng ở `30/100`, khởi động lại exact RC/targets, recapture health/runtime identity và mở window thay thế sạch ở `0/100`; không carry-forward request hoặc downtime.
  - [x] Dừng window-06 fail-closed trước card 018 sau khi xác nhận `7` calculation-invalid khiến manifest cũ không thể đạt `100` eligible.
  - [x] Chạy burst 100 request tuần tự và tombstone kết quả ở `throughput_evidence_only` sau khi canonical gate reject hash-domain mismatch; không carry-forward hoặc rerun.
  - [x] Sửa validator cho campaign tương lai tại `d77f28c`, giữ nguyên gate/artifact gốc.
  - [x] Ghi owner disposition lịch sử `deferred_pending_valid_pilot`; không sửa hoặc carry-forward artifact của window-06/burst-window-11.
  - [x] Owner chấp nhận prospective Grounded Math contract `grounded-math-3d-100-v1` ngày `2026-08-14`: đúng `100` eligible calculation request trên lịch freeze trước request đầu, từ dispatch eligible đầu tiên đến completion eligible thứ 100 tối thiểu `72` giờ, rolling cap `35/24 giờ`, concurrency 1 và không retry/replacement/catch-up.
  - [x] TDD cập nhật operator campaign/gate và canonical `grounded_math_pilot_gate.py` từ `7 ngày` + `15/24 giờ` sang contract Math `3 ngày` + `35/24 giờ`. Declaration, window và manifest mới bind immutable `pilot_contract_version=grounded-math-3d-100-v1`; cả hai gate từ chối artifact thiếu marker, legacy hoặc drift. Targeted coverage đạt trên 80%, full unit suite và ba trục review Standards/Spec/Security đã pass trước khi freeze tooling.
  - [x] Tạo declaration mới bind contract 3 ngày/100 và mở operator window-13 từ tooling commit đã review `066bee2`; campaign `19aacefbe67b1aa3907a490c` bắt đầu sạch ở `0/100`, request đầu tiên đã complete và Scheduled Task fail-closed đã tự kích hoạt thành công. Không reuse request, thời gian, trace hoặc gate của window-06, burst-window-11 hoặc pre-dispatch window-12.
  - [x] Human review đủ `20/20` case phân tầng theo signed `single_owner` bởi `bao.nguyen`, gồm đủ `6` mandatory-risk case; controlled-demo quality accepted.
  - [x] Fresh Math-only interaction matrix concurrency 1/5 pass `64/64`; owner technical review accepted; exact completed Math-only ledger đã ký và matching selective bundle đang live-authorized trên default rollout.
  - [x] Live rollback effect và same-bundle restart đã được chứng minh; bounded port-release wait được harden riêng, không sửa nóng signed Math bundle đang chạy.
- Phase 3 — Query-only manifest `10+3`, terminal no-render contract, trace binding, explicit external-processing policy, provider-boundary revalidation, deployment/restore readiness, atomic formal dispatch và preparation-packet binding đã được harden. Formal window `d424628` có pair 01 pass nhưng pair 02 fail duy nhất latency P95 ratio `2.538923543 > 1.5`; zero provider failure/retry, mọi non-latency gate pass và pair 03 không chạy. Sau disposition `keep_off_technical_limit_current_design`, owner đã mở architecture scope mới và batched branch retrieval + decomposed-only parent batch đã freeze tại `9e48ddc`. Window đầu tiên trên design mới `0318ded` tombstone trước preparation vì operator expiry-parse failure; authorization gate đã harden tại `802aa90`, full unit `3018/3018`. Window thay thế `6a566e1` pass preparation/boundary nhưng fresh smoke fail `0/5` với năm HTTP 502, zero retry, nên không có formal pair. Query vẫn OFF vì provider health và measured formal evidence trên design mới chưa đạt. Gate kế tiếp chỉ được mở sau independent recovery signal và fresh draft/authorization/root/preparation/boundary/smoke/declaration/trace/series; không same-window rerun, threshold relaxation hoặc carry-forward.
- Phase 4 — hoàn tất ở disposition `keep_off_technical_limit`. V2 trên `fe57f7d` chạy đủ `17/17` nhưng latency p95 ratio `2.226645` RED. V3 warm-state trên `32fc8d7` đóng giả thuyết cold-start đơn lẻ nhưng hai window đều dừng fail-closed vì Qdrant retrieval `ResponseHandlingException`: window-01 ở warm-up pair 1, window-02 sau warm-up sạch tại measured case 2. Không có đủ measured evidence cho formal; current evidence chưa chỉ ra code-local fix an toàn nếu giữ nguyên timeout/retry/fallback/oracle. `bao.nguyen` đã hoàn tất technical-review substitution và owner acceptance; Graph/formal/pilot/default tiếp tục OFF, không same-design rerun. Chỉ một future scope change được owner duyệt mới được mở declaration mới; formal khi đó vẫn bắt buộc `multi_reviewer/independent`.
- Phase 5 — case-paired diagnostic V3 đã được implement/review và root-fix trace path tại `27e4809`, nhưng clean window mới dừng sau `3/9` cặp vì một provider retry. Chưa có full-series latency/cost signal và chưa mở formal window; health recovery signal `5/5` không thay thế CRAG smoke hoặc declaration. Lượt kế tiếp vẫn cần authorization, declaration, smoke và window mới, không carry-forward.
- Phase 6 — Community Summaries tiếp tục OFF; Late Interaction tiếp tục `rejected/OFF`.

## Thay đổi activation contract

### Selective activation

Bổ sung chế độ `selective` vào contract hiện tại thay vì tạo mọi tổ hợp profile:

- Bundle vẫn dùng schema `rag-activation-bundle-v1`; trường `feature_flags` là tập flag chính xác được yêu cầu.
- CLI build bundle nhận `--profile selective` và `--enable-feature <RAG_*_ENABLED>` lặp lại.
- Renderer đọc flag trực tiếp từ bundle đã hash, không tin flag nhập thủ công.
- Runtime nhận `RAG_ACTIVATION_PROFILE=selective`; bundle, ledger, commit và chữ ký phải khớp chính xác.
- `all_off` giữ nguyên: không cần bundle hoặc chữ ký.
- Các profile cũ được giữ để tương thích, nhưng rollout mới dùng `selective`.

Các invariant fail-closed:

- CRAG và Claim Repair luôn cùng ON hoặc cùng OFF.
- Late Interaction luôn bị từ chối.
- Community Summaries chỉ được ON khi Graph Retrieval ON.
- Grounded Math, Query Decomposition, Graph Retrieval và cặp CRAG/Claim Repair không còn phụ thuộc nhau.
- Mọi flag ON phải có decision `accepted`; accepted set trong ledger phải bằng đúng enabled set trong bundle.
- Default rollout có feature ON vẫn bắt buộc chữ ký Ed25519 trên exact ledger bytes.

Cập nhật ba nguồn contract chính:

- [feature_activation.py](C:/Users/bao.nguyen/Documents/ChatBotProject/src/mech_chatbot/governance/feature_activation.py)
- [milestones.py](C:/Users/bao.nguyen/Documents/ChatBotProject/scripts/controlled_demo_eval/milestones.py)
- [matrix.json](C:/Users/bao.nguyen/Documents/ChatBotProject/data/integrated_hardening_v1/matrix.json)

Evaluation dependencies mới:

- Grounded Math, Query Decomposition, Graph Retrieval và CRAG chỉ phụ thuộc evaluation foundation.
- Community Summaries phụ thuộc Graph Retrieval.
- Query-only không được dùng CRAG branch correction; đây là một variant mới và phải có evidence riêng.
- Matrix chuyển thành `integrated-v3-selective`, sinh accepted stack từ release decisions thay vì chuỗi cumulative cố định.

Không làm admin UI để bật/tắt. Mọi thay đổi live vẫn qua bundle được duyệt, restart process và health verification.

## Các phase triển khai

### Phase 0 — Khóa governance và baseline mới

1. Viết test RED cho selective activation:
   - Math-only, Query-only, Graph-only và CRAG/Claim-only hợp lệ khi đủ evidence.
   - CRAG lệch Claim Repair, Late ON, Community không có Graph đều fail.
   - Flag rejected/keep_off không thể xuất hiện trong bundle.
   - Sai commit, schema, hash, signature hoặc bundle flags đều fail.
   - `all_off` vẫn hoạt động không cần bundle.
2. Implement thay đổi tối thiểu ở shared activation path.
3. Chuyển runners sang baseline độc lập:
   - Math: `all_off → math-only`.
   - Query: `all_off → query-only`.
   - Graph: `all_off → graph-only`.
   - CRAG: `all_off → CRAG + Claim Repair`.
   - Community: `graph-only → graph + community`.
4. Chạy targeted tests, full backend/frontend/build/security và coverage tối thiểu 80%.
5. Freeze commit governance mới.
6. Recapture all-off runtime, browser smoke, c1/c5 và rollback. Mọi evidence cũ chỉ còn giá trị lịch sử.

Không feature nào được bật trong phase này.

### Phase 1 — LAN pilot riêng

Tạo một cặp disposable target cho release train sau khi owner duyệt exact names:

- SQL: `Mech_Chatbot_DB_RestoreTest_RAGPilot_<YYYYMMDD>_<sha7>`.
- Qdrant: `TaiLieuKyThuat_v2_RestoreTest_RAGPilot_<YYYYMMDD>_<sha7>`.

Runtime pilot:

- Web: `0.0.0.0:8180`.
- RAG: `127.0.0.1:8200`.
- Runtime LAN chính tiếp tục dùng profile đã release trước đó; ban đầu là `all_off`.
- Không `WITH REPLACE`, không drop, không auto-cleanup.
- Có thể reuse hai target trong cùng release train nếu fingerprint dữ liệu không đổi; mỗi RC phải có read-only reconciliation receipt mới.
- Nếu dữ liệu hoặc Graph fingerprint thay đổi, cần owner duyệt target mới.

Mỗi pilot chỉ chạy một feature độc lập trước khi chạy accepted stack.

### Phase 2 — Grounded Math

Trạng thái ban đầu: lợi ích đã rõ; chưa cần tối ưu thuật toán.

1. Chạy lại math-only trên current commit:
   - Ba matched pair độc lập.
   - Candidate thêm duy nhất Grounded Math.
   - Exact calculation, formula, unit, citation và provenance đạt 100%.
   - Không unsupported number; tối đa một calculation.
   - Leakage bằng 0; latency ratio `<=1.25`; cost ratio `<=1.5`.
2. So sánh review contract với pack 10/10 hiện có:
   - Chỉ reuse nhãn khi immutable case và `review_contract_sha256` không đổi.
   - Case hoặc candidate output thay đổi phải review lại.
3. Chạy LAN pilot math-only:
   - Contract `grounded-math-3d-100-v1` hợp lệ từ `2026-08-14` là đúng 100 request có calculation route, với ít nhất `72` giờ từ dispatch eligible đầu tiên đến completion eligible thứ 100.
   - Freeze trước toàn bộ card, prompt hash và lịch dispatch; rolling cap `35/24 giờ`, concurrency 1, không retry/replacement/catch-up và dừng fail-closed khi declaration/runtime/gate drift.
   - Automated safety/citation/provenance check đủ 100.
   - Human review 20 case phân tầng và mọi failure.
   - Owner đã authorize mở future pilot theo contract này, nhưng chưa có pilot pass. Burst 100 request lịch sử không thỏa điều kiện 72 giờ, giữ nguyên tombstone và không mở review.
4. Nếu đạt, tạo release Math:
   - Main stack chuyển `all_off → {Grounded Math}`.
   - Rollback thông thường về `all_off`.

Chỉ sửa code nếu math-only recapture phát hiện lỗi deterministic ở shared calculation/provenance path. Không tối ưu thêm khi các gate vẫn xanh.

### Phase 3 — Query Decomposition

Trạng thái ban đầu: chất lượng tốt nhưng cost từng đạt `1.554088 > 1.5`.

1. Thêm telemetry metadata-only để chia token/cost thành:
   - planner;
   - từng branch retrieval/correction;
   - context cuối;
   - final generation.
2. Chạy diagnostic, không dùng làm formal evidence.
   - Diagnostic final `20260808-diagnostic-7b9d575-dirty-02` đã chạy đủ `13+13` case, provider failure bằng 0 và cost reconciliation hợp lệ; `-01` là tombstone của implementation diff trước review.
   - Kết quả chưa đạt: cost ratio `1.899837 > 1.35`; pass tổng `2/13 → 7/13`, decomposition pass `7/10`, branch accuracy `86.67%`, citation accuracy `70%`.
   - Final generation là overhead trội: baseline gọi `9` lần với cost `0.0198925`, query-only gọi `13` lần với cost `0.0377925`; chênh lệch `0.0179`.
   - Root fix hiện tại chuyển high-risk partial coverage có nhánh `grounded_negative` thành `insufficient_evidence` trước final generation; partial do `insufficient_evidence` hoặc `access_denied` không-grounded vẫn được phép sinh câu trả lời.
   - Diagnostic `20260808-diagnostic-7b9d575-dirty-03` là tombstone inconclusive: baseline và candidate đều có `9` provider failure, cost ratio `null`. Provider smoke riêng sau đó fail `0/5` với `ExternalProcessingDenied`, nên không chạy thêm 13+13 case và không dùng `-03` để đánh giá cost/quality.
- Evaluation runner hiện ép `EXTERNAL_PROCESSING_POLICY=all_external` cùng `RAG_EXECUTION_CONTEXT=evaluation` trong bản sao môi trường của subprocess; process cha, `.env`, runtime LAN và default rollout không đổi. Diagnostic `20260808-diagnostic-7b9d575-dirty-04` đã vượt local policy gate nhưng bị dừng sau 19 ProxyLLM error call, 13 retry event và 9 baseline trace; mọi provider call đều trả HTTP 503 `service_unavailable/no_capacity`, candidate chưa bắt đầu.
- Diagnostic runner hiện kiểm tra lại source commit, manifest, tracked worktree, runner hash và fixture fingerprint giữa hai arm. Nếu baseline đã có provider failure, runner ghi tombstone `inconclusive` rồi dừng trước candidate; recovery chỉ được xác nhận bên ngoài diagnostic, không bắt buộc thêm smoke.
- Root fix evaluator tại `a106bef` ưu tiên `debug.generation_metrics.decomposition_usage` current sau stream trước top-level snapshot cũ. Diagnostic sạch sau fix đạt cost ratio `1.319309`, final-generation call không tăng (`9 → 9`) và reconciliation pass ở cả hai arm.
- Formal pair 01 dừng ở branch accuracy/citation; pair 02/03 không chạy. Ba BOM case đang ghép kỳ vọng Grounded Math vào Query-only, còn `decomp-sufficient-missing` đang ghép partial-answer expectation vào terminal grounded-negative safety contract. Owner phải adjudicate/relabel và freeze manifest mới trước formal series; không sửa runtime/evaluator để làm yếu refusal hoặc citation oracle.
3. Áp dụng root fix theo thứ tự:
   - Nếu duplicate source/context chiếm phần lớn overhead: dedupe theo canonical source identity trước final context, nhưng giữ đủ citation cho từng branch.
   - Nếu shared instruction bị lặp: đưa phần chung ra khỏi từng branch.
   - Planner chỉ được gọi khi deterministic splitter không bao phủ đủ intent; câu đơn giản không gọi planner.
   - Sau root fix, theo owner không chạy thêm provider smoke cho diagnostic không-formal; chỉ mở một diagnostic mới khi provider được xác nhận hồi phục ngoài attempt này. Nếu diagnostic hợp lệ vẫn vượt ngưỡng, đo lại final-generation calls và per-case context trước khi tối ưu context; không giảm gate hoặc bỏ refusal/post-check để lấy số đẹp.
   - Nếu ba nguyên nhân trên không giải thích overhead, mở design investigation cho split-generation/merge; không sửa ngưỡng.
4. Diagnostic target là cost `<=1.35` để có margin; formal gate vẫn giữ `<=1.5`.
5. Freeze commit và chạy ba formal pair query-only:
   - Complex-answer gain `>=10%`.
   - Simple planner call bằng 0.
   - Branch và branch-citation accuracy 100%.
   - Tối đa ba subquery, một correction và một final generation.
   - Latency/cost `<=1.5`; không leakage, retry hoặc wrong-answer regression.
6. Human review 10-case current-contract pack.
7. LAN pilot query-only đủ 7 ngày/100 eligible requests.
8. Nếu đạt:
   - Chạy interaction matrix Math-only, Query-only và Math+Query.
   - Release accepted stack `{Math, Query}`; nếu Math chưa đạt thì release `{Query}`.
   - Rollback Query về stack accepted trước đó.

Mỗi formal failure được giữ làm tombstone. Vòng tiếp theo phải có design delta cụ thể, test RED và declaration mới.

### Phase 4 — Graph Retrieval

Trạng thái ban đầu: edge/provenance tốt nhưng khả năng tạo câu trả lời relational chưa đủ thuyết phục.

1. Giữ lại 21/21 review labels chỉ khi source queue hash và immutable edge content không đổi.
2. Khóa tối thiểu 10 relational cases thuộc ba domain Technical, Production và Maintenance, bao phủ:
   - version;
   - supersedes;
   - contains part;
   - uses material;
   - applies to.
3. Instrument từng seam: route → seed → traversal → hydration → final answer.
4. TDD sửa seam đầu tiên làm relation đúng nhưng answer/claim/citation sai; không thêm edge giả hoặc nới provenance.
5. Chạy ba graph-only matched pair:
   - Relational answer gain `>=10%`.
   - Reviewed precision `>=95%`.
   - Provenance/citation 100%.
   - Structured coverage `>=80%`.
   - Pending/stale serving bằng 0.
   - Tối đa hai hop và 50 edge.
   - Local/non-relational quality không giảm.
   - Latency và cost `<=1.5`.
6. LAN pilot graph-only đủ 7 ngày/100 routed relational requests.
7. Nếu đạt:
   - Chạy single, pairwise và full-stack matrix với Math/Query đã accepted.
   - Thêm Graph vào accepted stack.
   - Rollback Graph về stack accepted trước đó.

Tiếp tục vòng diagnose → TDD root fix → RC mới → formal window cho đến khi accepted hoặc feasibility review chứng minh không thể đạt answer gain mà vẫn giữ provenance/RBAC/budget.

### Phase 5 — CRAG + Claim Repair

CRAG không còn chặn Math, Query hoặc Graph.

1. Chạy interleaved diagnostic tách riêng:
   - retrieval;
   - rerank;
   - correction;
   - generation;
   - claim repair.
2. Không sửa code khi delta vẫn do provider variance.
3. Root-fix decision:
   - Rerank overhead: reuse kết quả không đổi và giảm candidate duplication, không giảm recall.
   - Generation/context overhead: dedupe corrected context, vẫn chỉ một final generation.
   - Correction overhead: tối ưu shared correction path nếu phần CRAG-controlled thực sự chi phối.
   - Provider variance: tiếp tục diagnostic với declaration mới; không chọn rerun đẹp.
4. Chỉ mở formal window khi diagnostic dự báo latency ratio `<=1.25`.
5. Ba formal pair phải đạt:
   - Candidate vượt baseline trên failure case và tất cả case đạt.
   - Correction và repair đều được exercise.
   - Mỗi loại tối đa một lần.
   - Wrong-refusal giảm; wrong-answer không tăng.
   - Leakage/provider error ngoài contract bằng 0.
   - Latency `<=1.25`, cost `<=1.5`.
6. LAN pilot chỉ đếm request thực sự đi vào correction/repair route; đủ 7 ngày/100 eligible requests.
7. Khi accepted, thêm cả hai flag vào accepted stack và chạy lại pairwise/full-stack matrix.

CRAG được tiếp tục qua nhiều design iteration, nhưng mỗi iteration phải có nguyên nhân mới hoặc thay đổi kỹ thuật đo được.

### Phase 6 — Community Summaries và Late Interaction

Community Summaries:

- Giữ OFF cho đến khi Graph accepted.
- Sau đó khóa graph fingerprint, generate/review summary và chạy ít nhất 10 global cases.
- Global gain `>=10%`; local/relational quality không giảm; citation/provenance đầy đủ; không stale/pending serving; latency/cost `<=1.5`.
- Nếu technical gate đạt, chạy pilot 7 ngày/100 global requests rồi mới thêm vào stack.
- Nếu lần đánh giá chuẩn đầu tiên fail, giữ OFF và đưa lại owner ưu tiên; Community không nằm trong nhóm “theo đến cùng”.

Late Interaction:

- Giữ `rejected` và OFF.
- Không sửa/tối ưu implementation hiện tại.
- Chỉ mở lại bằng design mới có giả thuyết retrieval cụ thể và predeclared NDCG window; không nới NDCG/recall/latency/storage gate.

## Progressive release và bật/tắt

Mỗi feature đi qua cùng lifecycle:

1. Diagnostic.
2. TDD root fix nếu có lỗi code-controlled.
3. Freeze một RC sạch.
4. Provider smoke 5/5, không lỗi/retry/fallback ngoài contract.
5. Ba formal pair và stop-on-first-failure.
6. Human review pack.
7. Controlled-demo bundle cho LAN pilot riêng.
8. Pilot đủ thời lượng theo capability contract và 100 eligible requests: mặc định 7 ngày; riêng Grounded Math prospective từ `2026-08-14` là tối thiểu 72 giờ.
9. Integrated interaction matrix.
10. `tran.nghi` review kỹ thuật.
11. `bao.nguyen` chấp nhận hoặc giữ OFF.
12. Default-rollout ledger được ký và tạo selective activation bundle.
13. Restart runtime LAN chính và xác minh health/browser/c1/c5.

Accepted stack sau mỗi release là hợp của mọi feature đã accepted. Feature chưa đạt không xuất hiện trong bundle.

Rollback có hai mức:

- Feature rollback: tạo bundle mới bằng accepted stack trước release, restart và smoke.
- Emergency rollback: xóa toàn bộ governed flags, chạy `all_off` không cần bundle.

Bất kỳ lỗi security, cross-scope leakage, bundle/commit drift hoặc rollback failure nào đều chuyển thẳng về `all_off`.

## Pilot review và acceptance

Mỗi pilot:

- Chạy đủ thời lượng theo capability contract và 100 request đúng route, lấy điều kiện hoàn thành sau. Mặc định là 7 ngày; riêng Grounded Math prospective từ `2026-08-14` là tối thiểu 72 giờ từ dispatch eligible đầu tiên đến completion eligible thứ 100, với lịch freeze và rolling cap `35/24 giờ`.
- Automated checks đủ 100 request: runtime identity, security, citation structure, provenance, budgets, provider errors và leakage.
- Quality gain vẫn lấy từ matched formal evaluation, không suy diễn từ organic traffic không có oracle.
- Human review 20 case phân tầng theo governance được ký trước pilot:
  - Math controlled-demo hiện tại dùng `single_owner`: đủ 20 primary labels bởi `bao.nguyen`.
  - Codex chỉ chuẩn bị metadata và hỗ trợ kỹ thuật; không được ghi là independent human reviewer.
  - Mọi failure, access-denied bất thường hoặc low-confidence case bắt buộc owner review.
  - Pilot khác hoặc default rollout không được kế thừa exception này nếu không có governance artifact đúng scope/commit.
- Không ghi raw document, credential hoặc raw private response vào artifact.

Pilot bị dừng ngay khi:

- Có leakage hoặc RBAC/site/security violation.
- Citation trỏ sai nguồn hoặc stale/pending data được serve.
- Runtime SHA/snapshot/provider/bundle drift.
- Provider fallback ngoài declaration.
- Rollback không đưa runtime về previous stack hoặc `all_off`.

Quality/budget failure làm design hiện tại `rejected`; provider outage làm window `inconclusive`.

## Interaction matrix và kiểm thử

Trên mỗi final RC, chạy tại concurrency 1 và 5:

- `all_off`;
- từng accepted feature riêng;
- mọi pair có liên quan tới feature mới;
- full accepted stack;
- Graph+Community khi Community được xét.

Kiểm tra:

- Backend unit/integration/eval và coverage line/branch tối thiểu 80%.
- Frontend unit, production build và browser E2E.
- Login, SSE, citation, access denied và CSRF/session failures.
- Strict streaming, cache namespace isolation và request budgets.
- SQL/Qdrant fingerprint, restore receipt và rollback.
- Zero leakage; không severe wrong-answer.
- Commit, deployment ID, snapshot, provider hash, collection và bundle hash ổn định trước/sau c1/c5.
- Không migration/data cleanup ngoài disposable scope.

Sau bất kỳ code change nào:

- Viết regression test trước.
- Freeze commit mới.
- Vô hiệu hóa formal evidence khác commit.
- Không overwrite/rerun tombstone để chọn kết quả đẹp.

## Điều kiện hoàn tất roadmap

Bốn tính năng chính chỉ kết thúc ở một trong hai trạng thái:

- `accepted`: formal gate, pilot, review, security, interaction matrix và rollback đều đạt.
- `keep_off_technical_limit`: có feasibility report chứng minh muốn tiếp tục phải phá ngưỡng latency/cost/safety/RBAC đã khóa hoặc thay đổi provider/corpus/product scope; `tran.nghi` review và `bao.nguyen` chấp nhận disposition.

Decision pack cuối phải phân biệt `implemented / measured / reviewed / pilot-authorized / live-authorized`, có disposition cho cả sáu tính năng và chỉ accepted stack được ký để bật.

## Giả định đã khóa

- Mục tiêu là Windows/LAN nội bộ, không Docker và không public deploy.
- Jina vẫn là reranker chính; Voyage fallback một lần rồi deterministic local fusion.
- Cả ba nhóm BOM/math, multi-intent và relational query đều có nhu cầu thực tế.
- `bao.nguyen` là release owner; `tran.nghi` vẫn là technical reviewer độc lập trước quyết định default rollout. Riêng Graph `keep_off_technical_limit`, `bao.nguyen` đã xác nhận là reviewer hợp lệ thay `tran.nghi`; substitution này không cấp quyền feature-on và không áp dụng cho feature/default-rollout khác. Math controlled-demo hiện tại dùng exception `single_owner` riêng, không thay thế gate default rollout.
- Không có UI toggle cho người dùng hoặc admin.
- Không nới threshold quality, safety, security, citation, provenance, budget hoặc rollback. Thay đổi thời lượng Grounded Math từ 7 ngày xuống 3 ngày là contract prospective do owner chấp nhận, không áp dụng hồi tố và không thay đổi các threshold kỹ thuật.
- Không tạo/xóa account; reuse cohort nội bộ hiện có.
- Không cleanup disposable targets tự động.
- Không push hoặc publish tracker/PR nếu chưa có phê duyệt riêng.
