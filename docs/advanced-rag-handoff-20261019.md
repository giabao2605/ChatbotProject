> Cập nhật20/10/2026: Query, Graph và Math đã bật không hết hạn cho14phòng ở `3ed1bfa44bf7ab39d3a8e132731e31fc59261f77`, grant `default_rollout`, `expires_at=null`. Runtime thực tế đã xác nhận cả3cờ và API tổng/so sánh/Graph. Giữ nguyên kiểm quyền, nguồn hiện hành, duyệt bằng chứng và độ đầy đủ; chưa có nghiệm thu nghiệp vụ14phòng. Xem [biên nhận bật lâu dài](../reports/rag-14-departments-20261005/permanent-core-20261020/README.md). Các mốc QA7ngày dưới đây là lịch sử đã được thay thế về thời hạn bật.

# Bàn giao Advanced RAG sang task mới — 19/10/2026

## Cập nhật tiếp tục ngày20/10/2026

Main/candidate hiện cùngcommit948aaf9, Query/Graph/Math đã rollout trên grantQA đến27/10 08:07 theo chỉ thị người dùng. Đọc [biên nhận hiện hành](../reports/rag-14-departments-20261005/full-features-rollout-20261020/README.md) trước checkpoint19/10 bên dưới. HTTP QueryPlan cố định đã nối phân loại một lượt hiện có; không mở paidbatch. Nghiệmthu0/14; người dùng chưa có đầu vào nghiệp vụ thật. Không biến demo thànhcâu thật, restarttest hoặc hỏi lại quyềnrollout; mục tiêu đầyđủ14phòng vẫn mở.

## Đọc trước và bắt đầu

Tiếp tục mục tiêu đầy đủ trong `docs/advanced-rag-14-departments-implementation-plan-20261005.md`, đặc biệt mục30 điều chỉnh cách làm. Không bắt đầu lại plan hoặc test từ đầu. Ưu tiên phát hành từng năng lực dùng được, sau đó mở rộng nguồn/nghiệm thu từng phòng. Không chờ đủ1.050câu mới phát hành phần độc lập; không tự hạ gate, bỏ kiểm quyền/số/phiên bản hoặc tự ghi nghiệm thu.

Việc đầu tiên: kiểm git/runtime/nguồn hiện tại, đối chiếu đường phát hành của mã đã chốt với các điều kiện thật còn thiếu. Chọn một bước triển khai cụ thể có tác dụng cho người dùng; làm xong phần chuẩn bị được phép trước khi hỏi quyết định còn thiếu. Không tiếp tục mở rộng batch giả lập Accountant hoặc sửa các lỗi ngoài đường phát hành chỉ để tăng test/count. Không tạo một vòng kiểm/benchmark/review mới cho mã không đổi.

## Workspace và mã đã xác minh

- Checkout chính: `C:/Users/bao.nguyen/Documents/ChatBotProject`; HEAD `f58d2e19c7a4e81b78f2909ffe9295ad33f06875`.
- Candidate có mã mới: `C:/Users/bao.nguyen/.codex/worktrees/rag-13-departments/ChatBotProject`; detached HEAD `28fc5b557d9d6d9396e388981e21696a01dc69c6`. Tiếp tục ở đây; không tạo checkout từ main rồi làm mất candidate.
- Chuỗi commit local: `bf7832b` Graph/Math+Graph27tệp; `65f6b271` ngữ cảnh dòng nguồn9tệp; `28fc5b5` tổng một scope/Query35tệp. Các quyền commit này đã dùng đúng phạm vi, không phải quyền commit vô hạn.
- Candidate product paths sạch khi bàn giao. WIP `.gitignore`, plan và các file coverage ngoài commit phải giữ. Main có WIP khác; không reset/clean/broadstage/cherry-pick đè công việc khác.
- Python: `C:/Users/bao.nguyen/Documents/ChatBotProject/chat_env/Scripts/python.exe`; PYTHONPATH cần candidate/src và candidate. Khi cần kiểm targeted, nạp bcrypt,pyarrow,onnxruntime,numpy trước pytest để tránh DLL lỗi lặp. Không chạy kiểm chỉ vì bàn giao.

## Trạng thái năng lực và giới hạn

- Bản chính lần xác nhận runtime17/10 chỉ bật Math so sánh hai nguồn QA: Doc1171v1 và1172v2,1500/1800VND,300VND,20%. Grant bind f58d2e1, hết hạn25/10/2026 13:28:49 Asia/Saigon; phải xác minh lại live, không coi trạng thái này là hiện tại chỉ dựa vào ghi chú.
- Query/Graph chưa được bật theo bằng chứng đã có. Graph SQL có21quan hệ/occurrence được người dùng duyệt,4declaration chưa đầy đủ ở Doc59/60/68/69;22native query trảpartial đúng nguồn. Chưa có quyền ký/bật Graph, contract/gate/Qdrant còn mở. Vật liệu201/304 và hai child-binding vẫn chưa duyệt; không tự suy cây đầy đủ.
- Candidate28fc5b5 có `sum_reviewed_scope`: một scope hoàn chỉnh đã duyệt, Decimal, một bước, schema/evaluator riêng, REST/SSE/UI/lịch sử kiểm quyền/phiên bản. Chỉ evaluation/test; chưa có production wiring/activation cho mẫu này.
- Query tối đa3nhánh, kết quả/nguồn riêng, shared budget/deadline, độc lập vẫn trả nếu nhánh khác thiếu; dependency thiếu bị chặn. Contract mới `query-plan-scope-total-v1` không kế thừa gate cũ.
- Opt-in nội bộ `semantic-scope-total-query-routing-v1-draft` tái dùng classifier một lượt, childbinding giữ scope/question. Chỉ evaluation/test, không tự chọn dòng/nguồn hoặc tính số bằng model. HTTP mặc định chưa opt-in: lỗi guard đếm dấu phẩy thành nhiều ý vẫn còn trên đường mặc định. Ba kịch bản CSV đã đạt dùng classifier giả lập, chưa chứng minh model thật.
- CRAG/ClaimRepair/Community/LateInteraction chưa được bật; CAD/3D, tích hợp ghi ngược phần mềm và cây lịch sử/lô/serial ngoài đợt này.

## Phê duyệt và provider

- User xác nhận đủ14phòng, tổng1050câu/75mỗi phòng; cho phép nguồn giả lập trước; ưu tiên triển khai trực tiếp main, không pilot riêng.
- User chốt mẫu tổng một scope và Query một lượt phân loại khi số tác vụ chưa rõ. Quyền triển khai không tự cấp production activation/sign/paidcall hoặc duyệt dữ liệu.
- Provider chính OpenRouter/GPT-5.6Luna; policy được gia hạn7ngày đến24/10,10surface cũ, chưa có query_decomposition. Xác minh lại khi cần. Mọi cửa sổ paidcall giả lập21/3/24/7/6 trước đã đóng; không tái dùng hoặc tự retry.
- `admin_bao` là người duyệt nguồn đã xác nhận. Không tạo reviewer/human acceptance. Các quyết định dữ liệu chưa duyệt phải giữpending.
- Không đọc/ghi log nội dung privatekey,token,.env; key cũ đã tìm được trong task, dùng công cụ/hồ sơ hiện có khi có quyền ký đúng phạm vi. Không sao chép credential vào bàn giao.

## Bằng chứng đủ để tiếp tục, không chạy lại

Đường dẫn dưới đây tính từ root candidate; các artifact mới đã được sao sang main để đọc.

- `reports/rag-14-departments-20261005/scope-total-query-20261019/candidate-review/commit-receipt.json`:35tệp đúng commit/hash; canonical ECC hook chạy, không bypass, khôngpush/deploy.280Pythonversionskhớp audit trước; cảnhbáodependencyvẫncòn, khôngclaimcleanaudit.
- Cùng thư mục cha: `README.md`, `semantic-routing/review.md`, `classified-combinations/execution.json`. Review không blocker; ba kịch bản nguồn chốt trả1500/500/950, thiếugiữa vẫn giữ độc lập, phụthuộcbịchặn;0provider. Coverage incremental chọnnhánh94,29%,semantic95,83%, từngmodule>=80% trên đúngpin; giữlượtfixturefailvàsửariêng, khôngđổinhãnlịch sử.
- `scope-total-implementation-20261019/README.md`:10câu tổng ACC001–010 trên nguồn đóng17/10; component/typecheck/browserfixture đã đạt; coverage standalone92,62%. Pin phiên bản trước Query vẫn có ý nghĩa lịch sử, không tự gắn vào mã mới.
- `graph-math-release-preparation-20261017/contract-review-proposal.md`:chờcontract/gate thật; proposalghi commitbf7832b lịch sử, không tự ký nó cho28fc5b5. Đọc chỗ cầnthiết, không auditlạitoànrepo.
- `primary-deployment-20261017/README.md`:activationMathQA/backup/rollback/providerpolicy cũ. `/api/health` mới làhealthAPI; `/health`cóthểlàSPA,401RAGkhôngphảihealthpass.

## Tiến độ câu hỏi: không đánh đồng đã chạy và đạt

- Draft150/1050:75Accountant+75Technical;12phòngchưasoạn/chưachạy.
- Technical42/75IDnguyênvăn với selection/plannerfixture lịch sử; chưađủmodel/nghiệpvụ.
- Accountant trước lượtcuối32/75ID:001–020,024–025,032,033,040,058,062,066,071–074.
- Lượtcuối `accountant-data-quality-execution-20261019/closed-v2/execution.json` đãhoàntấttrướckhinhậnlệnhdừng:8IDmới041,042,043,045,049,050,059,067+2đốichứng.10guard/numericchecksđạt nhưngExcelfilter/formulavàhướngdẫntừchốicụthểchưađượcchứngminh. ChỉtăngthốngkêIDđãthựcthi lên40nếubáocáocầnsố; khôngclaim8câuđạtđầyđủ. READMEbankvẫnghi32ởcheckpointtrước, chưareconcile.
- Do đó82IDcóbằngchứngthựcthilịchsử, khôngphải82câuđạt;968IDchưachạy. Nghiệmthu0/14. Khônglấysốunitcheckslàcâuhỏinghiệpvụ.
- `accountant-provenance-execution-20261019/execution/input.json` ACC054đãđóngnhưngassertfilenamecitationfail:ôB2/B3,row/hashđúng,executorcardfile_name=None. Chưasửa/chưarunlại, khôngtínhvào40hoặc82. Khảnănglỗiở_sourcesdùngchungscope/comparison/BOM; khôngnớiassertđểpass. Hoãnlạinếukhôngnằmtrênđườngpháthành.
- Lượtchuẩnbịdataqualityv1lỗifixturelocalegiữnguyên;v2dùnggiátrịreviewtườngminh,khôngsửaproduct/câuhỏi/oracle/CSVđểpass.

## Cách làm bắt buộc sau bàn giao

Ngắn gọn tiếng Việt, khôngicon. Khôngoverengineering. Giữ nguyênmục30plan:pháthànhtừngphần,táisửdụngkiểmchung,gộpquyếtđịnhcầnuser,sửatậnnguyênnhân,khôngđổiquestion/oracle/gateđểpass. Khôngmởrộngbộtestgiảlậpkhôngphụcvụrelease. Báokếtquảbằngtínhnăng/nguồndùngđược/blockercụthể,khôngkểtestcountliêntục.

Người dùng đã yêu cầu tự mở task mới và giao goal làm tiếp. Đã tạo task 01a151fd-863f-7612-b4cf-c2dd78d129b5, tên “Tiếp tục Advanced RAG 14 phòng — ưu tiên phát hành”; xác nhận turn mới đang chạy và đã đọc thông điệp bàn giao. Goal ở task cũ tạm dừng để tránh hai task cùng triển khai; không đánh dấu hoàn thành. Task mới được yêu cầu tạo goal giữ đầy đủ mục tiêu 14 phòng và phạm vi plan. Đây không phải quyền mới cho commit/ký/bật; các mốc phát hành từng phần chỉ là thứ tự ưu tiên.
