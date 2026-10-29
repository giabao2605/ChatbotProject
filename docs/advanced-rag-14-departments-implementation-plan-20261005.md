> Cập nhật20/10/2026: Query, Graph và Math đã bật không hết hạn cho14phòng ở `3ed1bfa44bf7ab39d3a8e132731e31fc59261f77`, grant `default_rollout`, `expires_at=null`. Runtime thực tế đã xác nhận cả3cờ và API tổng/so sánh/Graph. Giữ nguyên kiểm quyền, nguồn hiện hành, duyệt bằng chứng và độ đầy đủ; chưa có nghiệm thu nghiệp vụ14phòng. Xem [biên nhận bật lâu dài](../reports/rag-14-departments-20261005/permanent-core-20261020/README.md). Các mốc QA7ngày dưới đây là lịch sử đã được thay thế về thời hạn bật.

> Ghi chú lưu trữ 29/10/2026: các checkpoint bên dưới mô tả thời điểm được ghi. Bản web đã triển khai sau đó là `27800dc` (F01–F06). Nhánh bàn giao này lưu tài liệu và cấu hình skill; không đồng nghĩa triển khai thêm mã lên web.

# Kế hoạch hoàn thiện Advanced RAG cho 14 phòng ban

Ngày tạo: 05/10/2026. Ngày cập nhật: 20/10/2026.

**Hiện trạng 20/10/2026:** Query, Graph và Math đã rollout theo chỉ thị người dùng; main/candidate cùngcommit `948aaf90f120566ddbe285e37e6e5b7e82f5434e`. App http://127.0.0.1:8080/chat; grantQA đến27/10 08:07 giờ Việt Nam. Query tối đa3nhánh; HTTP QueryPlan cố định đã nối bộ phân loại tối đa1lượt hiện có khi cần. Không đo lại model hoặc tạo nghiệmthu từ fixture. [Biên nhận hiện hành](../reports/rag-14-departments-20261005/full-features-rollout-20261020/README.md).

Math có hai scopeQA; Graph có22pin/21quan hệ đã duyệt và vẫn trảpartial; BOM không trả tổng toàn cây thiếu căn cứ. Các trạng thái cũ bên dưới giữ làm lịch sử. Rollout không biến Graph formal/Qdrant/ba miền thànhđạt.

**Còn thiếu để hoàn thành toànplan:** nguồn/câu hỏi/người phụ trách và duyệt nghiệp vụ thật cho14phòng; bộ75câu khác nghĩa mỗi phòng và nghiệmthu>=90% theo gate gốc; xác nhận vật liệu201/304, lá/bản vẽ con/binding và độ đầy đủ đangpending; formalGraph/Qdrant, ma trận tíchhợp và quan sát sửdụng thực. Người dùng đã cho rollout bằng QA đang có, chưa cung cấp đầu vào nghiệp vụ thật và yêu cầu không mở vòngtestlặp; không đổi các mục thiếu thànhđạt. CRAG/ClaimRepair vẫn chờgate riêng, Community chờ bài toán tổngquan, Late giữtắt theoplan.

**Cập nhật kiểm chứng ngày 19/10:** App8080 và RAG8100 không kết nối được tại lần kiểm gần nhất; chưa xác nhận Math đang phục vụ. Candidate đã commit phần tổng một scope tại `d7aee7f`; delta 18 tệp cho tổng và so sánh cùng runtime còn chờ duyệt. Grant so sánh và hai nguồn QA đã lưu vẫn hợp lệ khi kiểm, khác với tình trạng runtime. Nghiệm thu nghiệp vụ vẫn 0/14; kết quả ngày 17/10 dưới đây là bằng chứng lịch sử.

**Trạng thái hiện tại 17/10/2026 13:30: Math đã bật trên bản chính** theo phê duyệt trực tiếp của người dùng, chỉ cho hai nguồn QA Doc1171v1/1172v2 và mẫu so sánh hai phạm vi. Bốn giá trị, hai phạm vi đầy đủ và quy tắc VND/ROUND_HALF_UP2số do `admin_bao` (UserID81) duyệt; grant ký đúng commit/scope/snapshot và hết hạn25/10/2026 13:28:49. HTTP thật trả đúng1500/1800/300/20%, chiều ngược−16,67%; citation/tải nguồn đúng phiên bản/hash, không đăng nhập401, zero model calls. [Kết quả triển khai và cách dùng](../reports/rag-14-departments-20261005/primary-deployment-20261017/README.md).

Người dùng đã duyệt gate bổ sung `math-v2-primary-qa-identity-binding-v1`: giữ bằng chứng thuật toán f58d2e1 ở snapshot cũ và kiểm riêng ánh xạ hai nguồn byte-identical/quyền/phiên bản/phép tính trên SQL chính; không đổi nghĩa gate hoặc kết quả lịch sử. Không chạy lại full suite/build/benchmark. Query/Graph và các advanced flag khác vẫn tắt. Nghiệm thu nghiệp vụ0/14; theo dõi7ngày/100request thực hiện trên bản chính, không tạo pilot riêng hoặc tính hai request kiểm của Codex vào lưu lượng người dùng. Phạm vi mới chưa bao gồm nguồn kế toán thật hoặc Math tự tìm nguồn. Các checkpoint dưới đây giữ trạng thái tại lúc ghi; phần “chờ duyệt/chưa bật” trước13:30 đã được cập nhật bởi trạng thái hiện tại này.


Ưu tiên cập nhật theo yêu cầu ngày16/10: giảm kiểm thử lặp và tập trung đường phát hành. Mốc bật thử đầu tiên là Math v2 so sánh hai phạm vi nguồn được chọn rõ ràng và đã duyệt; chưa bao gồm planner tự chọn nguồn, Query v2 hoặc BOM/Graph v2. Đây là phát hành từng phần trong phạm vi plan, không thay mục tiêu đầy đủ14 phòng hoặc tự cấp quyền bật live.

Quy tắc kiểm thử từ checkpoint này:

Theo yêu cầu lược bỏ kiểm thử không cần thiết: sau khi nhóm ảnh hưởng và luồng tích hợp còn thiếu đã đạt, chuyển sang việc phát hành đang còn thiếu. Không tự mở thêm vòng kiểm, thêm người review hoặc bộ benchmark chỉ để tăng mức chắc chắn chung chung.

- Chạy đúng nhóm bị ảnh hưởng sau một thay đổi logic; không mặc định chạy lại toàn bộ backend/frontend, mọi E2E hoặc mọi ma trận.
- Giữ các kiểm tra trực tiếp về số học, nguồn/phiên bản, phân quyền, chữ ký/quyền kích hoạt và rollback. Giữ ngưỡng coverage cho logic thay đổi cùng các gate phát hành đã chốt.
- Tái sử dụng kết quả đã có khi mã/nguồn/quyền liên quan không đổi; không tạo fixture hay runner trùng để tăng số đếm.
- Một review tập trung khi phần thay đổi đã hoàn chỉnh; chỉ review lại phần được sửa do finding mới. Không nhân các vòng review trên cùng mã.
- Thay đổi tài liệu/metadata không kích hoạt test sản phẩm hoặc build. Không tự chạy full regression chỉ vì đến mốc tích hợp/phát hành; chỉ mở rộng khi thay đổi dùng chung có nguy cơ ảnh hưởng ngoài nhóm đã kiểm, và phải ghi rõ nguy cơ đó.
- Mỗi lượt kiểm phải trả lời một câu hỏi còn thiếu cho quyết định sửa hoặc bật tính năng. Nếu đã có bằng chứng còn hiệu lực cho cùng hành vi, cùng đường xử lý và phần mã liên quan không đổi thì bỏ lượt chạy trùng; không xóa regression test đang bảo vệ lỗi cũ.
- Với mốc Math v2 đầu tiên, gộp API, chat/citation, lưu lịch sử và browser thành một luồng trên SQL staging; đo concurrency 1/5 và rollback trên cùng backend. Không dựng thêm runner hoặc ma trận SQLite tương đương. Chỉ chạy lại bước thất bại và bước bị bản sửa ảnh hưởng.
- Không gọi model để kiểm lại phép so sánh cố định không dùng model. Với nhánh có model, chỉ đo lại khi bản sửa tác động routing hoặc đầu ra cần đo; không mua lượt gọi mới để xác nhận thay đổi tài liệu, giao diện hoặc runner thuần túy.
- Contract v2 đề xuất cho so sánh cố định không yêu cầu provider smoke vì không gọi model; có provider call ngoài dự kiến thì gate từ chối. Ngoại lệ này cần duyệt cùng contract trước live; giữ nguyên kiểm tra provider của v1 và các tính năng có dùng model.
- Bằng chứng offline, chạy model, nghiệm thu và quyền phát hành vẫn tách biệt; giảm số lượt chạy không có nghĩa bỏ lỗi hoặc hạ tiêu chí đạt.

Checkpoint 17/10: [Math v2 API/browser trên SQL staging](../reports/rag-14-departments-20261005/math-v2-pilot-20261016/README.md) đã trả và lưu đúng bốn kết quả/hai revision nguồn; nhóm HTTP 1/5 đạt, tải nguồn kiểm hash và phiên đăng nhập. Chỉ mở lại bước lịch sử sau khi sửa selector test bị sai, không chạy lại full suite. Thu hồi callback quyền thử chặn request mới; signed bundle rollback và full startup triển khai còn thiếu. Quyền thử được tiêm riêng, dữ liệu synthetic, không coi là activation hoặc nghiệm thu 14 phòng.

[Hồ sơ bật thử Math đầu tiên](../reports/rag-14-departments-20261005/math-v2-release-wiring-20261016/governance/first-release-proposal-20261017.md) chốt hai scope synthetic, contract/budget, ngưỡng và các bước phát hành còn thiếu. Preflight 17/10 chỉ đọc đã kiểm lại pin/nguồn SQL và cập nhật ledger DRAFT; chưa có release commit, formal pair, startup/rollback triển khai hoặc chữ ký/nghiệm thu. Cấu hình checkout chính vẫn có bảy cờ advanced tắt tại lúc đọc; chưa kiểm runtime đang phục vụ từ thông tin cấu hình này. Không chạy thêm test/build/model cho việc chuẩn bị tài liệu.

Cập nhật sau checkpoint: người dùng đã duyệt commit local theo candidate và mã được chốt ở `8e7bb1458276241a3057c5d70392a9ba0aede542`. App/RAG đã chạy đủ lifespan với SQL Server và Qdrant local, health 200 và đóng sạch; chưa phải cloud/TCP hoặc signed grant startup/rollback. [Duyệt contract Math v2 ngày 17/10](../reports/rag-14-departments-20261005/math-v2-release-wiring-20261016/governance/contract-user-approval-20261017.json) chốt so sánh hai nguồn/bốn bước, miễn provider smoke cho đường không gọi model, giữ ngưỡng và pilot 7 ngày/100 request sau khi bắt đầu. Xác nhận này không cấp quyền live, không tạo chữ ký hoặc nghiệm thu nghiệp vụ; formal baseline/candidate và quyết định phát hành vẫn phải có bằng chứng riêng.

[Candidate SQL theo commit đã chốt](../reports/rag-14-departments-20261005/math-v2-pilot-20261016/candidate-20261017/README.md) đã đóng config/actor/source/reference trước chạy và thực thi hai chiều so sánh, tám bước đúng: 1500→1800 cho +300/+20%, đảo chiều cho −300/−16,67%. Zero provider/vector calls, nguồn hiện hành và quyền được đọc lại trong snapshot. Đây là candidate-only synthetic, chưa có baseline hoặc formal pair đạt; không tăng số câu bank/phòng nghiệm thu và không dùng candidate làm baseline. Không chạy thêm full suite/build/model cho checkpoint này.

Checkpoint nghiệp vụ 17/10: [BOM lấy số lượng từ lệnh sản xuất có nguồn](../reports/rag-14-departments-20261005/bom-order-quantity-20261017/README.md) đã nối kho chứng cứ/duyệt/API/UI với executor. Mapping khóa root revision/hash, product/site và kiểm quyền cả hai đầu; literal cũ giữ hash/wire cũ. ACC-032 dùng lệnh 7 thành phẩm và BOM hai tầng, ba occurrence lá, trả đúng 203,00; không có quyền bản vẽ con thì chặn tổng/nguồn trong ca SQLite. Bổ sung một luồng browser/API trên SQL Server staging thật: duyệt nguồn bằng cookie/CSRF, chat đúng tổng, bốn download đúng hash/revision và mở lại lịch sử đạt. Đã sửa lỗi cạnh chung cho hai occurrence cùng mã và callback làm rơi metadata khi lưu lịch sử; chỉ kiểm phần ảnh hưởng cùng luồng tích hợp này. Giữ riêng dữ liệu giả lập và bộ câu hỏi v1 đóng; chưa tính nghiệm thu, model routing, concurrency BOM, full startup production hoặc quyền bật BOM/Graph từ ca này.

Đây là bản cập nhật của kế hoạch 13 phòng ban trong task này, tổng hợp toàn bộ nội dung kế hoạch người dùng yêu cầu triển khai và các xác nhận phạm vi sau đó. Người dùng đã xác nhận con số 13 là nhớ nhầm và yêu cầu tính đủ 14 phòng nghiệp vụ. Bản này giữ nguyên phạm vi kỹ thuật đã duyệt; mọi chỗ nghiệm thu 13 phòng được thay bằng 14 phòng. Việc triển khai đang thực hiện, tài liệu này không phải bằng chứng nghiệm thu hoặc quyền bật tính năng trên môi trường thật.

Kế hoạch rollout cũ trong repo: [Advanced RAG theo hướng value-first](advanced-rag-value-first-rollout-plan.md). Tài liệu này cập nhật phạm vi và công việc triển khai theo nội dung task hiện tại; tài liệu cũ giữ các contract, checkpoint và bằng chứng lịch sử. Các điểm mâu thuẫn về rollout hoặc ADR phải được đối chiếu ở Giai đoạn 0, không tự thay đổi quyền phát hành đã ghi nhận.

[Graph occurrence nhiều trang — checkpoint 17/10](../reports/rag-14-departments-20261005/graph-cross-page-occurrences-20261017/README.md) đã sửa ba điều kiện SQL để mỗi vị trí BOM giữ chứng cứ trang/dòng riêng dù dùng chung cạnh cụm–chi tiết. 84 kiểm tra ảnh hưởng đạt; một lượt SQL Server rollback-only trả đủ ba dòng và tổng 203,00, đồng thời chặn chứng cứ số lượng sai trang. Không thêm migration/cạnh trùng hoặc đổi Graph v1. Sửa này chưa commit, chưa kiểm tự trích PDF nhiều trang hay tạo nghiệm thu/activation; không ảnh hưởng phạm vi contract Math so sánh đã duyệt.

[Ràng buộc baseline Math v2 — checkpoint 17/10](../reports/rag-14-departments-20261005/math-v2-baseline-binding-20261017/README.md) đã chặn số đo baseline thiếu observation riêng, sửa latency/cost hoặc mượn candidate. 30 kiểm tra ảnh hưởng đạt, helper mới coverage dòng 100%, review không có finding; ngưỡng và gate v1 giữ nguyên. Chưa có formal pair thật. Đường template v2 khi tắt flag trong evaluation vẫn không phải baseline; phần đo tiếp theo cần đường chatbot thường và nguồn/cấu hình đóng trước chạy. Không gọi model, sửa bằng chứng cũ, tạo acceptance hoặc bật feature trong checkpoint này.

[Chuẩn bị đối chứng Math v2 — checkpoint 17/10](../reports/rag-14-departments-20261005/math-v2-baseline-preparation-20261017/README.md) đã kiểm đường chatbot thường bằng SQL/Qdrant local: lấy đúng Doc1/2, dừng trước API OpenRouter/Luna, zero provider calls. Đã sửa truyền cấu hình policy của adapter sang audit generation; 42 kiểm tra ảnh hưởng đạt, review không có finding. Bộ chạy ba cặp/hai chiều và patch delta tám tệp đã chuẩn bị; đề xuất tối đa sáu lần gọi Luna cho baseline, candidate không gọi model, policy/audit riêng 60 phút, không retry và dừng khi lỗi. Chưa có quyền commit delta/call mới, chưa chạy formal, không đổi quyền phát hành hoặc contract Math đã duyệt.

[Sửa composition policy — checkpoint 17/10](../reports/rag-14-departments-20261005/math-v2-baseline-preparation-20261017/recovery-01/README.md): người dùng đã duyệt và mã delta tám tệp được commit ở `cc298b90eded410836d7f1cc2515fd4aea15c74a`. Lượt formal đầu dừng trước API do default adapter chưa nhận ExternalAiSettings; SQLite xác nhận zero call/audit row và policy đã đóng. Đã sửa default builder tại composition root, giữ builder được tiêm; 21 kiểm tra ảnh hưởng đạt, review không có finding. Preflight sau sửa qua audit SQLite thật rồi client guard dừng trước mạng, evaluation-only không mở production. Chưa chạy lại lượt đã đóng; đề xuất delta ba tệp/cửa sổ mới vẫn pending, formal pair bằng 0 và feature chưa bật.

[Sửa toán hạng so sánh/trace — checkpoint 17/10](../reports/rag-14-departments-20261005/math-v2-comparison-operands-20261017/README.md): commit ba tệp đã chốt ở `222b5629b134b3355fcc66be4212a218d9a6357e`. Lượt mới thực hiện hai call Luna thành công rồi dừng vì candidate chiều thuận bị đếm nhầm “kỳ A và kỳ B” thành hai tác vụ; pair01 không đạt, policy đã đóng. Đã sửa bộ tách ý chung nhưng vẫn chặn bốn tác vụ độc lập, đạt 198 kiểm tra liên quan và hai chiều SQL offline đúng bốn bước, zero provider calls. Đã ngăn diagnostics Math v2 đi vào emitter pilot v1, giữ contract/gate và historical evidence; 40 kiểm tra trace/legacy đạt. Các diff được review không có finding; patch mới chưa commit, chưa đo lại model, formal pair đạt vẫn bằng 0.

[Ba cặp đối chứng Math v2 đạt — checkpoint 17/10](../reports/rag-14-departments-20261005/math-v2-baseline-preparation-20261017/recovery-02/formal-run-01/README.md): delta năm tệp đã được người dùng duyệt và commit ở `f2678811836cfdb1a272d0c40d3919e20bde0f39`. Đã chạy đúng sáu call Luna baseline, không retry; cả ba gate đạt, sáu candidate/24 bước đúng, candidate không gọi model. P95 tập synthetic là baseline6078ms/candidate31ms; không suy ra tải hoặc chất lượng doanh nghiệp. Audit sáu started/success, policy đã đóng; không tái dùng các cửa sổ lỗi. Hồ sơ owner review đã chuẩn bị draft trên đúng commit/snapshot/nguồn; chưa có quyết định/chữ ký release hoặc signed startup/rollback/live window. Formal vẫn pending_review/acceptedfalse, không tạo nghiệm thu 14 phòng hoặc bật feature.

Rà soát hồ sơ ký sau ba cặp phát hiện public key authority ở commit lịch sử `279a6a1` chưa tích hợp vào source `f267881`. Đã khôi phục đúng public key vào worktree, chưa commit, không sinh key mới hoặc đọc private key. Packet giữ trạng thái thiếu key trong frozen commit và quyền release false; không gán ba cặp cũ sang source mới. Preflight chuẩn bị lần sau kiểm key trong exact commit trước khi cấp policy/gọi model. Phần tích hợp authority và ký/startup/rollback còn thiếu, chưa bật tính năng.

Đã tìm lại private key authority do Codex lưu ngoài repo và chỉ nạp trong bộ nhớ để đối chiếu public key: khớp hoàn toàn Ed25519 lịch sử, không sao chép/in khóa hoặc tạo chữ ký. [Đề xuất tích hợp public key](../reports/rag-14-departments-20261005/math-v2-release-wiring-20261016/governance/public-key-integration-20261017/proposal.json) chỉ gồm key công khai và bản cập nhật plan; runtime/dependency/nguồn/câu hỏi không đổi. Quyền commit hai tệp và cửa sổ API tiếp theo chưa có; contract hiện vẫn yêu cầu bằng chứng theo exact source commit, không tự gán formal f267881 sang commit mới.

Checkpoint mới nhất 17/10: public key authority đã commit đúng hai tệp được người dùng duyệt tại `f58d2e19c7a4e81b78f2909ffe9295ad33f06875`. [Ba cặp trên commit có key](../reports/rag-14-departments-20261005/math-v2-baseline-preparation-20261017/release-authority-integration/formal-run-01/README.md) đều đạt, sáu candidate/24 bước đúng; đúng sáu call Luna baseline thành công, policy đã đóng, candidate zero call. P95 của tập synthetic sáu mẫu là baseline6218ms/candidate16ms, không suy ra tải hoặc chất lượng doanh nghiệp. Đã tạo checkout staging sạch cùng commit và hồ sơ nguồn/hash; [đề xuất ký/startup/rollback](../reports/rag-14-departments-20261005/math-v2-release-wiring-20261016/governance/owner-review-f58d2e1-20261017/signed-staging-proposal.md) giới hạn SQL test, hai scope synthetic, một cửa sổ tối đa60phút, không provider call. Chưa ký, chưa mở grant/cửa sổ live, chưa bật chatbot thật; quyền commit/call vừa duyệt không bao gồm những thao tác này. Không chạy lại unit/E2E/build vì delta chỉ public key/tài liệu. Các checkpoint cũ giữ nghĩa tại thời điểm ghi; trạng thái mới này thay cho phần còn thiếu public key/formal của chúng.

Checkpoint ký/startup/rollback 17/10: người dùng đã duyệt dùng authority cũ cho SQL staging tối đa60phút, single_owner và zero provider calls. [Một lượt bật/tắt đã đạt](../reports/rag-14-departments-20261005/math-v2-release-wiring-20261016/governance/owner-review-f58d2e1-20261017/signed-startup/README.md) trong13,609giây: health có Math grant hợp lệ, một câu/bốn kết quả cùng citation đúng; vô hiệu selected bundle chặn request409; restart all-off và đóng sạch. Cửa sổ staging đã đóng, không tái dùng; chữ ký giữ làm bằng chứng, còn hiệu lực mật mã đến expiry60phút đã ghi. Không còn service/grant được chọn; không gọi model, sửa DB chính hoặc bật chatbot đang dùng. Không chạy thêm test/review/benchmark. Các blocker public key, formal và signed local startup/rollback cho template so sánh đã được giải quyết; rollout thực tế, topology triển khai, pilot/dark launch và nghiệm thu14phòng còn riêng, không tuyên bố toàn plan hoàn tất.

Triển khai bản chính 17/10 theo chỉ dẫn mới của người dùng: “không, không cần pilot riêng, cứ trực tiếp bản chính mà triển khai”. [Bản chính đã triển khai](../reports/rag-14-departments-20261005/primary-deployment-20261017/README.md) tại checkout gốc, commit `f58d2e1`, database `Mech_Chatbot_DB` lênV0047 sau backupCOPY_ONLY/CHECKSUM/VERIFYONLY; giữ nguyên số dòng cũ, WIP và cấu hình provider, không chép nguồn synthetic staging. App/RAG/worker chính đang chạy, HTTP200 và asset UI đúng hash; không build/test/model thêm. Ba tài liệu bảng tính hiện có đã được trích11giá trị chờ duyệt, chưa có scope được xác nhận đầy đủ. Vì vậy bảy cờ advanced vẫn tắt; activation Math trên primary còn cần nguồn được duyệt và grant đúng primary, Query/Graph cần gate riêng. Chỉ dẫn này cấp quyền triển khai mã/schema/runtime chính, không tạo nghiệm thu14phòng hoặc đổi nghĩa chữ ký/số đo synthetic.

Policy bản chính sau triển khai: người dùng đã đồng ý gia hạn policy OpenRouter/GPT-5.6 Luna hiện hữu7ngày. Đã cập nhật duy nhất metadata duyệt/expiry đến24/10/2026 13:13:16 theo giờ SQL Server, giữ model, credential,10surface cũ, retention và quyền nguồn. Không chạy test có phí, không mở surface planner mới hoặc bật advanced flag. [Receipt và kết quả](../reports/rag-14-departments-20261005/primary-deployment-20261017/provider-policy-renewal-result.json) tách khỏi các cửa sổ API synthetic đã đóng; quyền vận hành bình thường không tạo human acceptance hay quyền Math từ dữ liệu pending.

Làm rõ chặn activation Math sau triển khai: người dùng xác nhận tài khoản `admin_bao`, SQL xác nhậnUserID81 active và quyềnadmin/reviewer; không thêm reviewer hoặc quyền.11giátrị đã trích từ primary đều làQA torque/thời gian/version/ôtrống, không tạo cặp doanh thu tương thích. Đã chuẩn bị [hai nguồn QA tương thích và đề xuất điều chỉnh gate có phiên bản](../reports/rag-14-departments-20261005/primary-deployment-20261017/math-primary-source-proposal/README.md): giữ ba cặp thuật toán f58d2e1 đúng snapshot cũ, kiểm riêng ánh xạ nguồn không đổi bytes/quyền/phiên bản/phép tính trên primary, không gọi model; chỉ áp dụng haiCSV tổng1500/1800VND và runtime không đổi. Đề xuất đang chờ duyệt, chưa áp hợp đồng mới, nạp nguồn, tự duyệt hoặc ký/bật Math. Strict assay có khác biệt với hồ sơ ứng dụng vì grant phòng đã lưu trữ còn trongUserDepartments; đây không phải lỗi đăng nhập và chưa sửa bảo mật/regex chỉ để passTEC-054.

## 1. Mục tiêu và nguyên tắc

Xây dựng hệ thống trả lời có căn cứ, xử lý câu hỏi nhiều ý, tính toán chính xác và truy vết quan hệ bản vẽ cho toàn bộ 14 phòng ban. Nghiệp vụ kỹ thuật dưới xưởng là trọng tâm chuyên sâu; kế toán và các phòng còn lại được nghiệm thu độc lập.

Ba năng lực ưu tiên là **Graph Retrieval, Query Decomposition và Grounded Math**, dựa trên nền đọc tài liệu, dữ liệu có cấu trúc, phiên bản và phân quyền đáng tin cậy.

- Giai đoạn này xử lý PDF/ảnh, bảng kê, Excel/CSV và bản xuất từ phần mềm nghiệp vụ.
- Dữ liệu tính toán có thể đến từ nhiều định dạng hoặc phần mềm kế toán. Chưa kết nối trực tiếp hoặc ghi ngược phần mềm nghiệp vụ.
- File CAD/3D gốc triển khai sau; chưa xây bộ đọc hoặc giao diện 3D.
- Tái sử dụng SQL Graph, pipeline ingestion, giao diện duyệt, citation và bộ đánh giá hiện có. Không thêm graph database hoặc hệ thống PLM.
- Giữ nguyên đường xử lý và gate cũ. Khả năng vượt giới hạn hiện hành phải có hợp đồng đánh giá mới, không đổi nghĩa bằng chứng lịch sử.
- Bật chọn lọc tính năng đã đạt; không yêu cầu mọi tính năng cùng hoàn thành.
- Giữ nguyên công việc không liên quan trong checkout/worktree khác. Không tự commit, push, áp migration lên môi trường thật hoặc đổi cấu hình runtime.

## 2. Danh sách 14 phòng ban đã xác nhận

Danh mục dưới đây là danh sách 14 phòng nghiệp vụ đã được người dùng xác nhận lấy đầy đủ trong task này.

| STT | Mã phòng ban trong hệ thống | Nghiệm thu tối thiểu |
|---|---|---|
| 1 | Accountant | 75 câu hỏi thật |
| 2 | HR | 75 câu hỏi thật |
| 3 | HSE_5S | 75 câu hỏi thật |
| 4 | ISO | 75 câu hỏi thật |
| 5 | IT | 75 câu hỏi thật |
| 6 | Maintenance | 75 câu hỏi thật |
| 7 | Molding | 75 câu hỏi thật |
| 8 | Planning | 75 câu hỏi thật |
| 9 | Production | 75 câu hỏi thật |
| 10 | Purchasing | 75 câu hỏi thật |
| 11 | QualityControl | 75 câu hỏi thật |
| 12 | Sales | 75 câu hỏi thật |
| 13 | Technical | 75 câu hỏi thật |
| 14 | Warehouse | 75 câu hỏi thật |

Tổng tối thiểu **1.050 câu hỏi thật**, không trùng nghĩa trong bộ nghiệm thu của từng phòng. Một bộ fixture chung không thay thế bộ nghiệm thu nghiệp vụ của 14 phòng.

Các mã phòng QA đang có trong danh mục được ghi riêng là dữ liệu kiểm thử, không được tính vào 14 phòng nghiệp vụ. Mã CHUNG có trạng thái hoạt động không đồng nhất giữa các trường danh mục; ghi nhận riêng để rà soát, không tự xóa, sửa trạng thái hoặc coi là phòng thứ 15.

## 3. Các giai đoạn triển khai

### Giai đoạn 0 — Chốt hiện trạng, phạm vi và bộ nghiệm thu

**Công việc**

- Tạo worktree riêng từ HEAD của checkout chính tại thời điểm triển khai. Đối chiếu các delta cần thiết từ worktree Advanced RAG và QA; chỉ tích hợp thay đổi liên quan đã được review, giữ nguyên công việc khác.
- Ghi nhận commit, cấu hình, dependency, runtime và bằng chứng đang có. Phân biệt kết quả offline, pilot và quyền phát hành.
- Phạm vi đã được người dùng xác nhận là 14 phòng trong mục 2. Mọi thay đổi tiếp theo phải được đối chiếu với danh mục thực tế và xác nhận; không tự bỏ hoặc tạo phòng.
- Mỗi phòng xác định người phụ trách nghiệp vụ, người duyệt, nhóm tác vụ chính và **ít nhất 75 câu hỏi thật không trùng nghĩa**. Mỗi câu có đáp án/căn cứ, quyền người hỏi và mức độ nghiêm trọng khi trả lời sai.
- Đóng phiên bản bộ câu hỏi, đáp án, nguồn và quyền trước khi chạy nghiệm thu. Không tạo người duyệt, câu hỏi thật hoặc chữ ký nghiệm thu bằng dữ liệu giả.
- Dùng 47 PDF mẫu làm bộ kiểm thử kỹ thuật bổ sung. Ghi rõ tài liệu/cụm nào chưa đầy đủ; không coi sáu thư mục là sáu cây sản phẩm hoàn chỉnh.
- Thống nhất lại kế hoạch rollout và ADR còn mâu thuẫn: activation chọn lọc, dependency thực tế, giới hạn request, phiên bản gate và quy trình duyệt.

**Điều kiện hoàn thành**

Có danh mục phạm vi, baseline tái lập được và bộ nghiệm thu đóng phiên bản. Phòng thiếu dữ liệu được đánh dấu chưa đủ điều kiện; không chặn phát triển các phần độc lập.

### Giai đoạn 1 — Đọc đúng tài liệu và bảo toàn chứng cứ

**Công việc**

- Kết hợp lớp chữ, bố cục bảng và ảnh trang. Khi số lượng, vật liệu hoặc ký hiệu quan trọng thiếu/mâu thuẫn, đưa trường đó vào luồng kiểm tra hiện có.
- Lưu giá trị gốc, giá trị chuẩn hóa, đơn vị, phương pháp trích xuất và vị trí nguồn: tài liệu/phiên bản/trang/vùng hình hoặc file/sheet/ô/dòng.
- Phân biệt mã thành phẩm, bán thành phẩm, chi tiết, công đoạn, phiên bản và số tờ. Không suy diễn quan hệ từ tên thư mục hoặc hậu tố `Model1`, `Model2`.
- Bảo toàn số thứ tự bảng kê và liên kết với chú thích trên hình. Trình tự lắp ráp phải theo bước được ghi, không theo thứ tự văn bản trích xuất.
- Với Excel/CSV và bản xuất phần mềm, giữ bảng có kiểu dữ liệu và định danh dòng để tính toán; văn bản tìm kiếm là đầu ra bổ sung.
- Dữ liệu số thiếu, xung đột hoặc nguồn chưa được duyệt không được dùng làm căn cứ tính toán hay quan hệ phục vụ.
- Trích xuất tạo dữ liệu chờ duyệt. Người duyệt thật có thể sửa trường đã trích, xem nguồn và lưu lịch sử duyệt mà không sửa file gốc.

**Điều kiện hoàn thành**

Các mẫu mất số lượng, vật liệu mâu thuẫn và thứ tự sáu bước được nhận diện đúng; người duyệt có thể xem nguồn và sửa trường dữ liệu mà không sửa file gốc.

### Giai đoạn 2 — Quan hệ bản vẽ và cấu trúc sản phẩm

**Công việc**

- Tách ba nhóm quan hệ: cấu trúc cụm–chi tiết, thứ tự công đoạn, và thay thế phiên bản.
- Tái sử dụng `CONTAINS_PART` và truy vết ngược. Bổ sung liên kết có chứng cứ, đã duyệt từ mã chi tiết/cụm tới tài liệu bản vẽ/BOM hiện hành trong đúng phạm vi sản phẩm.
- Số lượng gắn với từng vị trí xuất hiện trong cụm. Không khử trùng theo mã chi tiết và không cộng các công đoạn thành nhiều chi tiết vật lý.
- Bản vẽ con chưa có hoặc chưa xác định được được ghi là chưa giải quyết; không tự tạo quan hệ như thể đã đủ nguồn.
- Giữ Graph hiện tại ở giới hạn cũ. Xây contract Graph mở rộng riêng: tối đa **8 mức cụm–chi tiết và 500 quan hệ được xét cho toàn request**; tối đa **50 quan hệ** đưa vào ngữ cảnh sinh câu trả lời.
- Chạm giới hạn, gặp chu trình chưa xử lý hoặc thiếu chứng cứ về độ đầy đủ phải trả trạng thái chưa đầy đủ. Không tiếp tục nhiều lượt ngầm để vượt giới hạn.
- Tính toàn cây chỉ dùng manifest occurrence đầy đủ đã xác nhận. Ngân sách 50 quan hệ cho LLM không phải tập dữ liệu tính tổng.
- Giai đoạn này phục vụ nguồn hiện hành được duyệt. Tra cây sản phẩm tại thời điểm lịch sử hoặc theo lô/serial là phạm vi sau.
- Bổ sung đo đạc Qdrant theo từng bước để giải quyết blocker cũ trước khi mở lại đánh giá Graph. Không tăng timeout/retry hoặc bỏ mẫu lỗi để đạt gate.

**Điều kiện hoàn thành**

Trả lời được chứa gì, dùng ở đâu, qua những tài liệu nào và phạm vi ảnh hưởng đã xác nhận; có đường dẫn chứng cứ và trạng thái đầy đủ rõ ràng.

### Giai đoạn 3 — Query và Grounded Math dùng chung

**Query**

- Giữ tối đa **ba nhánh độc lập**. Không âm thầm ghép/cắt ý để vừa giới hạn; câu vượt phạm vi phải yêu cầu thu hẹp.
- Mỗi nhánh có kết quả, nguồn và trạng thái riêng. Nhánh độc lập đủ căn cứ vẫn trả được; nhánh phụ thuộc dữ liệu thiếu bị chặn.
- Dùng chung deadline, hủy request và ngân sách xử lý; câu đơn giản không phát sinh planner không cần thiết.
- Câu rõ ràng vượt ba tác vụ độc lập bị chặn trước planner. Khi số tác vụ chưa rõ, cho phép tối đa một lượt planner để phân loại; nếu vượt ba tác vụ, phải yêu cầu thu hẹp trước truy xuất/tính toán. Câu đơn giản giữ đường xử lý nhanh; không ghép/cắt ý hoặc gọi provider không cần thiết.

**Grounded Math**

- Giữ nguyên Math v1. Giữ hai mẫu Math v2 đã có và bổ sung mẫu tổng theo xác nhận ngày19/10:
  1. So sánh hai phạm vi: tổng A, tổng B, chênh lệch và tỷ lệ thay đổi.
  2. Tổng hợp BOM từ manifest occurrence đầy đủ, có cơ sở số lượng rõ ràng.
  3. Tổng một scope đã duyệt: cộng đủ tập dòng được xác nhận, một bước Decimal, làm tròn sau tổng theo policy; contract/đánh giá `grounded-math-v2-scope-total-evaluation-v1` riêng. Không planner, biểu thức tùy ý hoặc tự suy điều kiện lọc. Quyền triển khai mẫu mới không bao gồm commit/API có phí/ký/live, không tái sử dụng gate hai mẫu cũ.
- Mẫu so sánh có tối đa **bốn bước nghiệp vụ** được ghi nhận riêng; không thêm bộ thực thi biểu thức tùy ý hoặc vòng lặp agent.
- Đa nguồn phải đóng phạm vi dữ liệu và snapshot. Mỗi nguồn giữ phiên bản riêng; không yêu cầu các tài liệu độc lập có cùng số revision.
- Tiếp tục dùng `Decimal`; quy tắc làm tròn và đơn vị phải được khóa theo nghiệp vụ. Không tự quy đổi tiền tệ hoặc đơn vị chưa có quy tắc được duyệt.
- Mẫu số bằng 0: vẫn trả các tổng/chênh lệch hợp lệ, ghi tỷ lệ không xác định.
- Không tính tổng từ top-k đoạn truy xuất. Thiếu dòng, phạm vi hoặc cơ sở số lượng phải chặn kết quả phụ thuộc.
- Gate v2 đánh giá từng bước, nguồn và đầu ra; không ghi nhiều phép tính thành một phép tính để vượt gate v1.
- Contract mở rộng chỉ được đánh giá trong phạm vi được phép trước khi đủ bằng chứng, duyệt và bundle phát hành; kết quả offline không tự cấp quyền phục vụ live.

**Điều kiện hoàn thành**

Math và Query đạt riêng, sau đó đạt kết hợp trên câu hỏi kỹ thuật, bảng số liệu và kế toán có đáp án nghiệp vụ.

### Giai đoạn 4 — Kiểm thử tích hợp và phát hành từng phần

- Đo riêng Math, Query, Graph; mọi cặp và tổ hợp ba tính năng phải có bằng chứng trước khi bật tương ứng.
- Ma trận gồm ba tính năng riêng, ba cặp và một tổ hợp ba. Ghi đúng commit của baseline và candidate; so sánh trên cùng dữ liệu, quyền và cấu hình đo, chỉ khác thay đổi đang đánh giá; đo concurrency 1 và 5.
- CRAG + Claim Repair được đánh giá tiếp trên nhóm lỗi truy xuất/thiếu căn cứ còn lại. Chỉ bật sau gate riêng và kiểm tra tương tác với stack đã chấp nhận.
- Community Summaries chờ Graph và bài toán tổng quan được chứng minh; Late Interaction giữ tắt trong đợt này.
- Chuẩn bị kỹ thuật có thể chạy song song; rollout phòng ban vẫn tuân theo thứ tự và prerequisite đang có.
- Giữ contract pilot hiện hữu; contract Graph/Math mở rộng phải được đóng phiên bản và duyệt trước cửa sổ live đầu tiên.
- Phát hành bằng selective bundle đúng commit, snapshot, ledger và chữ ký; kiểm chứng health, browser, chat/citation, concurrency 1/5 và rollback.
- Lỗi nghiêm trọng về số liệu, phiên bản hoặc phân quyền phải dừng phần rollout liên quan. Không tuyên bố hoàn thành **14 phòng** khi còn phòng chưa nghiệm thu.

## 4. Thay đổi dữ liệu và interface

- Mở rộng chứng cứ có cấu trúc để chứa vị trí nguồn, giá trị gốc/chuẩn hóa, trạng thái duyệt, xung đột và phạm vi dữ liệu.
- Bổ sung định danh occurrence, liên kết chi tiết–tài liệu và cơ sở số lượng; dùng migration bổ sung, bảo toàn dữ liệu lịch sử.
- Kết quả Graph/Math/Query có trạng thái đầy đủ, một phần hoặc chưa xác định; kèm công thức/bước tính và chứng cứ được phép xem.
- Giữ tương thích REST/SSE hiện tại bằng trường bổ sung có phiên bản. UI cũ vẫn đọc được; UI mới hiển thị phần thiếu, xung đột và bảng nguồn có phân trang.
- Duyệt trường dữ liệu và quan hệ qua luồng quản trị hiện có; không tự tạo reviewer hoặc tự ghi human acceptance.
- Kiểm tra quyền cả hai đầu quan hệ và nguồn dẫn. Metadata độ đầy đủ không được tiết lộ tên, mã hay số lượng tài liệu bị hạn chế.
- Migration được thử trên staging và có backup trước áp dụng thật. Runtime rollback bằng flag/bundle/epoch; không phụ thuộc xóa dữ liệu mới.

## 5. Kiểm thử và tiêu chí nghiệm thu

| Nhóm | Kiểm thử bắt buộc |
|---|---|
| Bản vẽ | Bảng kê 19 dòng; số lượng mất khi trích chữ; mâu thuẫn Inox 201/304; chú thích gắn vùng hình; trình tự sáu bước. |
| Quan hệ | Nhiều tầng, truy ngược, chi tiết dùng chung, nhiều occurrence, mã trùng khác phạm vi, bản vẽ thiếu, chu trình, vượt giới hạn. |
| Phiên bản | Nguồn bị thay thế/thu hồi; đổi revision; cache và quan hệ cũ không tiếp tục phục vụ. |
| Math | Đúng số, đơn vị, làm tròn, cơ sở số lượng; không cộng trùng công đoạn; hai kỳ; mẫu số 0; thiếu dòng; tập lớn hơn 50 quan hệ. |
| Query | Một–ba ý, vượt ba ý, nhánh thiếu, câu phụ thuộc, hủy/timeout, citation đúng từng ý. |
| Phân quyền | Khác phòng/site/clearance; nguồn chung; hai người có quyền khác nhau hỏi cùng sản phẩm; kiểm cả văn bản, SSE và metadata. |

**Ngưỡng chốt**

- TDD cho thay đổi logic; unit, integration và E2E các luồng quan trọng. Coverage tối thiểu **80% cho phần logic thay đổi**, không dùng coverage tổng để che module trọng yếu thiếu kiểm thử.
- Mỗi phòng có ít nhất **75 câu thật được xác nhận**; tỷ lệ hoàn thành tác vụ đúng tối thiểu **90%**, đồng thời phải đạt các gate hiện hữu nếu nghiêm hơn.
- Toàn bộ ca Math và ca bắt buộc có rủi ro cao phải đạt đáp án hoặc hành vi từ chối đã định trước. Từ chối một câu đủ dữ liệu không được tính là đúng.
- Không có rò quyền, số liệu trọng yếu thiếu căn cứ hoặc sử dụng sai phiên bản trong bộ nghiệm thu.
- Giữ nguyên ngưỡng chất lượng, latency và cost của từng gate. Ngưỡng tăng chất lượng không đạt thì giữ tính năng tắt; không sửa bộ câu hỏi sau khi xem kết quả.
- Đo baseline và candidate trên cùng dữ liệu, cấu hình và quyền; báo cáo từng phòng cùng thời gian thực hiện tác vụ, P50/P95 và chi phí.
- Dark launch theo checklist hiện hữu **3–5 ngày**; pilot capability giữ thời lượng/số request theo contract riêng, không thay thế lẫn nhau.

## 6. Kết quả double-check và ranh giới hoàn thành

Kế hoạch gốc đã được rà soát độc lập hai lượt và bổ sung:

- Cầu nối đã duyệt giữa mã chi tiết và bản vẽ cụm con.
- Phân biệt đầy đủ dữ liệu nguồn với đầy đủ kết quả tìm kiếm.
- Phân biệt occurrence, công đoạn và phiên bản để tránh tính trùng.
- Tách Math v2/Graph mở rộng khỏi giới hạn và bằng chứng v1.
- Ngăn metadata về phần thiếu làm lộ nguồn ngoài quyền.
- Giữ nghiệm thu riêng từng phòng; fixture demo không thay thế dữ liệu thật.

Bản cập nhật này sửa phạm vi thành **14 phòng nghiệp vụ đã xác nhận** và **1.050 câu hỏi thật tối thiểu**. Các cổng phát hành, giới hạn xử lý, ngưỡng chất lượng và ranh giới nghiệp vụ của kế hoạch gốc giữ nguyên.

Hoàn thành khi từng phòng trong danh sách 14 đã đạt bộ nghiệm thu của mình, stack bật thực tế có đủ bằng chứng tích hợp và rollback đã được kiểm chứng. Báo cáo cuối ghi rõ tính năng được bật, tính năng tiếp tục tắt và lý do.

File 3D, kết nối trực tiếp/ghi ngược phần mềm nghiệp vụ và tra cấu trúc lịch sử theo lô/serial nằm ngoài đợt triển khai này. Mọi cleanup có xóa dữ liệu/capture phải đúng phạm vi đã được phê duyệt; không tái sử dụng cửa sổ pilot đã kết thúc.

## 7. Baseline triển khai và việc còn cần để nghiệm thu

- Checkout gốc: `C:\Users\bao.nguyen\Documents\ChatBotProject`.
- Commit khởi điểm: `34cf24954faa78308aef985237ddb2228a225e85`.
- Worktree triển khai riêng: `C:\Users\bao.nguyen\.codex\worktrees\rag-13-departments\ChatBotProject`. Tên worktree được tạo khi phạm vi còn ghi 13; phạm vi nghiệp vụ trong tài liệu và nghiệm thu hiện là 14.
- Bộ mẫu bổ sung: 47 PDF trong sáu thư mục `9.1.00678`, `9.3.03843`, `9.3.03844`, `9.3.03951`, `9.3.04068`, `9.3.04080` tại checkout gốc.
- Query, Graph v2, Math v2 và trích xuất chứng cứ đang được triển khai/kiểm thử riêng trong worktree. Không coi kết quả fixture là đủ điều kiện phát hành.

Checklist nghiệm thu còn phải có bằng chứng:

- [ ] Người phụ trách nghiệp vụ và người duyệt thật cho từng phòng.
- [ ] Bộ câu hỏi thật, đáp án, nguồn, quyền và mức độ rủi ro đóng phiên bản: ít nhất 75 câu/phòng.
- [ ] Dữ liệu số, liên kết bản vẽ và khai báo đầy đủ phạm vi đã được duyệt.
- [ ] Migration trên staging, backup và kiểm tra tương thích dữ liệu lịch sử.
- [ ] Unit/integration/E2E và coverage của các module thay đổi.
- [ ] Bộ mẫu kỹ thuật bắt buộc và kiểm tra phân quyền, phiên bản.
- [ ] Gate riêng và ma trận tương tác của stack dự kiến bật.
- [ ] Contract mở rộng được duyệt trước cửa sổ live mới.
- [ ] Selective bundle đúng commit/snapshot/ledger/chữ ký.
- [ ] Kiểm chứng live, concurrency 1/5, rollback và dark launch theo contract.
- [ ] Báo cáo đạt/chưa đạt cho từng phòng, không gộp kết quả để che phòng chưa đạt.

## 8. Bổ sung ngày 08/10/2026 — Bộ câu hỏi do agent chuẩn bị

Theo xác nhận của người dùng, tổng bộ câu hỏi là **1.050 câu, 75 câu cho mỗi phòng**, triển khai và kiểm thử lần lượt từng phòng. Bắt đầu với Technical vì bản vẽ, BOM và quan hệ cha–con là trọng tâm; tiếp theo Accountant cho Grounded Math, rồi đi theo thứ tự rollout và prerequisite hiện hữu.

Agent tự soạn câu hỏi từ tài liệu sẵn có và các tình huống nghiệp vụ rõ ràng. Mỗi câu lưu định danh, ý nghĩa kiểm thử, nguồn, hành vi/đáp án kỳ vọng, mức rủi ro và trạng thái chạy. Câu thiếu căn cứ được giữ pending; số liệu fixture phải ghi rõ là giả định. Không nhân bản cách diễn đạt của cùng một câu để đủ 75.

Bộ câu hỏi do agent soạn là đầu vào chuẩn bị kiểm thử. Kết quả chạy câu hỏi, kiểm tra trích xuất nguồn và kết quả regression được ghi riêng; một test được nhiều câu tham chiếu không chứng minh các câu đó đã được trả lời đúng. Bộ này không tự trở thành câu hỏi thật đã được người nghiệp vụ xác nhận, cũng không tạo human acceptance hoặc quyền phát hành.

Các file từng phòng và tiến độ được lưu trong `reports/rag-14-departments-20261005/department-question-banks/`. Ngưỡng nghiệm thu, quyền, phiên bản và gate trong các phần trên giữ nguyên.

## 9. Cập nhật contract Query ngày 10/10/2026

Người dùng đã đồng ý cập nhật quy tắc xử lý Query trong task này: khi chưa rõ số tác vụ, cho phép tối đa một lượt planner để phân loại, rồi chặn câu quá ba tác vụ trước truy xuất hoặc tính toán. Câu rõ ràng quá ba tác vụ vẫn bị chặn trước planner; câu đơn giản giữ đường xử lý nhanh. Đây là điều chỉnh chính sách xử lý, không phải quyền bật tính năng trên môi trường thật.

Mọi nhánh phải giữ nguyên yêu cầu, thứ tự, dependency, kết quả, trạng thái và nguồn riêng. Lượt phân loại dùng chung deadline/cancellation/ngân sách request; không replan hoặc thực thi thêm vòng ngầm. Planner không tạo số liệu, quyền truy cập, nguồn hoặc bằng chứng; executor vẫn kiểm nguồn hiện hành đã duyệt, snapshot và phân quyền. Câu mơ hồ/không hỗ trợ hoặc plan không hợp lệ không được âm thầm bỏ ý.

Gate và bằng chứng v1 giữ nguyên. Việc duyệt chính sách này không thay đánh giá chất lượng semantic, ngưỡng latency/cost, nghiệm thu từng phòng, gate tích hợp, contract phát hành, bundle/ledger/chữ ký, pilot/dark launch hoặc rollback. Candidate hiện tại vẫn là draft test/evaluation với selection tường minh; không tự đổi version hoặc activation sau xác nhận này.

## 10. Checkpoint triển khai ngày 14/10/2026

Đã bổ sung chọn dòng chi tiết có lý do loại subtotal, chặn mã giao dịch thiếu/mơ hồ khi header đã khai báo, semantic mixed candidate và chứng cứ kết quả tính cho nhánh giải thích. Mọi kết quả vẫn là offline; không tự tạo nghiệm thu hoặc bật tính năng.

Candidate giải thích giữ kết quả số do backend Math xuất và dùng tên bước trong phần diễn giải. Số liệu của nhánh tra cứu độc lập phải trích nguyên câu kèm nguồn; chưa chứng minh số viết bằng chữ hoặc chất lượng model thật. Đây là giới hạn triển khai cần được kiểm riêng, không thay ngưỡng hay định nghĩa gate trong plan.

[Tiến độ và bằng chứng hiện tại](../reports/rag-14-departments-20261005/progress-computed-dependency-20261014.md) phân biệt mã đã làm, kiểm thử offline và phần nghiệm thu còn thiếu. Danh sách vẫn đủ 14 phòng, mục tiêu 1050 câu; từng phòng chỉ được tính hoàn thành khi đạt bộ nghiệm thu của mình.

Đã bổ sung phép đo embedding/RPC trong fallback Qdrant bằng adapter hiện có; 104 ca liên quan đạt. Chẩn đoán chỉ đọc 60 truy vấn chắc chắn rỗng có đủ mẫu và độ trễ, nhưng chưa là benchmark có dữ liệu hoặc gate Graph. [Checkpoint fallback và chẩn đoán thực](../reports/rag-14-departments-20261005/progress-hybrid-fallback-20261014.md) giữ riêng các giới hạn; không thay ngưỡng, snapshot hay quyền phát hành.

## 11. Checkpoint Technical ngày 15/10/2026

Đã chạy thêm 7 câu nguyên bản qua runner candidate hiện có với SQL fixture đóng và planner giả lập; Technical có 42/75 ID đã từng qua fixed runner, không phải 42 câu đã nghiệm thu. 11 bước Math được kiểm độc lập. Scenario TEC-065 kiểm ba nhánh và citation qua REST/SSE riêng; chưa là model/NL test. [Tiến độ Technical](../reports/rag-14-departments-20261005/progress-technical-recovery-20261015.md) giữ các gap nguồn/geometry, fast path TEC-054 và nghiệm thu còn thiếu. Không đổi gate, oracle đóng hoặc phạm vi 14 phòng.

Đã sửa bộ chạy đánh giá dùng số câu được yêu cầu thay mẫu số ghi cứng 24; 130 kiểm tra liên quan đạt, không sửa bằng chứng lịch sử. [Chuẩn bị routing Technical](../reports/rag-14-departments-20261005/progress-technical-routing-preparation-20261015.md) giữ đủ bảy câu và các giới hạn: TEC-028 chưa được đáp ứng bằng việc liệt kê quan hệ, TEC-054 chưa đạt đường không planner, nguồn từng ý phải được giữ. Tại checkpoint chuẩn bị, đề xuất chưa có authorization; số câu và nghiệm thu không tăng từ việc chạy lại fixture.

Người dùng sau đó duyệt tối đa bảy lần gọi; đã thực thi đủ bằng Luna, không retry, đóng toàn bộ policy riêng. [Kết quả model](../reports/rag-14-departments-20261005/progress-technical-luna-20261015.md) giữ điểm routing **2/7**; năm Math record/11 bước đúng căn cứ. TEC-028 chọn sai loại tác vụ; bốn ca khác không khớp tập đầu ra kỳ vọng. Đã bổ sung hướng dẫn khả năng template dùng chung, đạt 203 kiểm tra V4/V5 nhưng chưa đo lại model sau sửa. Không sửa nhãn của lượt đã đóng, không dùng lại authorization, không bật flag hoặc tạo nghiệm thu.

Đã bổ sung [chú thích bản vẽ và liên kết bước qua duyệt](../reports/rag-14-departments-20261005/progress-drawing-notes-20261015.md): giữ vùng chữ riêng, bắt buộc lựa chọn rõ bước hoặc ghi chú chung, kiểm quyền/phiên bản ở hai đầu và làm mất hiệu lực xác nhận cũ khi chứng cứ thay đổi. Nguồn mẫu có năm note được đóng crop/hash nhưng vẫn pending; mapping trong test không thay human acceptance. Không suy liên kết từ khoảng cách và chưa bao phủ leader/ký hiệu/scan toàn diện. Kiểm chứng và giới hạn được ghi riêng trong checkpoint, không tăng số câu nghiệp vụ đã đạt.

Kiểm chứng mã cuối cho phần chú thích: 4.935 backend test, 202 frontend test và hai luồng Chromium giả lập đạt; 511 hash Python không đổi trong lượt backend đầy đủ. Nguồn, review và liên kết được kiểm offline; chưa có SQL Server/staging/browser nối backend thật hoặc nghiệm thu nghiệp vụ. Không dùng các kết quả này thay gate hoặc cho phép activation.

Đã sửa [đọc trùng Graph khi tính BOM phụ thuộc](../reports/rag-14-departments-20261005/progress-graph-manifest-reuse-20261015.md): chỉ dùng manifest đầy đủ đã duyệt của tiền nhiệm trực tiếp, cùng request/actor/snapshot và phạm vi, giữ fingerprint/query gốc cho citation/history. 300 occurrence chỉ đọc một lần và tính đủ 1.800 trong fixture; giới hạn 8/500/50 giữ nguyên. 394 kiểm tra liên quan và 11 ca bổ sung history đạt; chưa chạy full suite trên hai runtime file mới hoặc đo model lại. Đây là checkpoint kỹ thuật, không tăng số câu nghiệm thu hoặc bật feature.

Đã kiểm lại [năm kịch bản an toàn](../reports/rag-14-departments-20261005/progress-technical-safety-20261015.md) về chống sửa Math, hủy, timeout, snapshot và hai actor qua REST/SSE. Sửa một checkpoint trong vòng citation để hủy/deadline chặn citation tiếp theo; 243 kiểm tra liên quan và 7 ca adapter cuối đạt. Kịch bản giữ SQL/oracle đóng, không thay nhãn lịch sử, không tính thành câu tự nhiên đạt hoặc gate live. Concurrency SQL Server, tải thực và nghiệm thu còn cần bằng chứng riêng.

Đã bổ sung [đường nhanh và đầu ra so sánh](../reports/rag-14-departments-20261005/progress-comparison-output-20261015.md): lệnh thuần có danh sách đầu ra được nhận toàn câu, không planner khi đủ một lựa chọn; renderer không in thêm kết quả số ngoài phần đã yêu cầu. Ledger/evaluator vẫn giữ đủ bốn bước của Math v2. 417 kiểm tra liên quan đạt; TEC-054 vẫn có planner và chưa có kiểm nhất quán tiền đề số với manifest. Không thay contract giới hạn, bằng chứng lịch sử, số câu nghiệm thu hoặc activation.

Đã thực hiện [chẩn đoán Qdrant có dữ liệu/embedding](../reports/rag-14-departments-20261005/progress-qdrant-populated-20261015.md) qua đường native hiện có:20request/40query,89points giữ nguyên hash, một query timeout được HYBRID fallback phục hồi. P95 retrieval4,39giây, dense embedding189ms; giữ cả mẫu chậm/lỗi. Chưa tách được server với transport, chưa có SQL-authoritative serving hoặc matched baseline/candidate. Blocker Graph giữ mở; không tăng timeout/retry, đổi cấu hình/index/collection hoặc dùng chẩn đoán thay gate.

Đã chốt [chẩn đoán HTTP và ưu tiên hoàn tất](../reports/rag-14-departments-20261005/progress-qdrant-http-timing-20261015.md): 20 query trả dữ liệu nhưng snapshot cuối timeout, nên lượt đo INCOMPLETE và Graph chưa đạt gate. Dừng mở thêm vòng đo ở nhánh hiện tại; ưu tiên 33 ID Technical còn thiếu bằng nguồn/runner hiện có, sửa lỗi dùng chung với regression trực tiếp. Giữ phạm vi đủ 14 phòng và các điều kiện nghiệm thu gốc.

Kiểm tra tích hợp mã hiện tại sau các sửa Graph/Math/Query: [5.001 test offline](../reports/rag-14-departments-20261005/integrated-final-20261015/unit.xml) đạt, không lỗi/skip; 514 file runtime/test không drift. [Trạng thái](../reports/rag-14-departments-20261005/integrated-final-20261015/status.json) không tạo acceptance hoặc activation. Không dùng bộ offline thay gate model/live hay nghiệm thu 14 phòng.

Đã đóng [đầu vào đo lại bảy câu Technical](../reports/rag-14-departments-20261005/technical-luna-preparation-20261015-v3/README.md), giữ nguyên labels/case/SQLite của lượt trước; 519 pins khớp hiện tại và 514 pins khớp lượt 5.001 test. Zero provider calls, đề xuất bảy lần gọi mới đang chờ duyệt. Không tái sử dụng cửa sổ cũ hoặc biến fixture thành kết quả model/nghiệm thu.

[Lượt bảy lần gọi mới đã đóng](../reports/rag-14-departments-20261005/progress-technical-luna-v3-20261015.md): routing3/7, phần tăng là nhận đúng unsupported, chưa chứng minh cải thiện tác vụ hỗ trợ. 4 Math records/10 bước thực thi đúng nguồn; TEC-045 bị chặn span_text. Bảy policy đã xác minh IsActive=0; không retry/tái sử dụng. Cần đối chiếu hợp đồng outputs với metadata bắt buộc trước khi sửa prompt tiếp, giữ nguyên mọi nhãn/kết quả lịch sử.

Đã sửa [độ đầy đủ Graph và hướng dẫn planner](../reports/rag-14-departments-20261005/graph-required-evidence-20261015/README.md) ở hai runtime file: trạng thái nguồn không phụ thuộc planner, hướng dẫn Graph thống nhất citations/coverage bắt buộc. 232 kiểm tra liên quan đạt, Graph coverage100% dòng/nhánh, review không có finding. Chưa đo model sau sửa; giữ kết quả3/7, lỗi span_text và mọi gate/activation như trước.

Đã thực thi [ACC-071: hai phép so sánh độc lập](../reports/rag-14-departments-20261005/accountant-dual-comparison-20261015/README.md) bằng bốn CSV/SQLite giả lập, không sửa runtime. 4/4 kiểm tra plan tường minh/planner giả lập đạt: giữ 8 bước và nguồn riêng, thiếu nhánh chi phí vẫn trả doanh thu. Accountant12 ID đã qua executor, không phải 12 câu model đạt; draft150/1050 và nghiệm thu0/14 không đổi. Không có lần gọi provider hoặc activation mới.

Đã thực thi [ACC-040: đối chiếu số lượng kiểm kê](../reports/rag-14-departments-20261005/accountant-inventory-comparison-20261015/README.md): 7/7 tình huống đạt, đúng 100/95/-5/-5%, không quy đổi pcs thành tiền; khác unit/phạm vi, thiếu dòng hoặc nguồn chưa duyệt bị chặn. Item/kho/cutoff phụ thuộc định nghĩa phạm vi được reviewer khai báo đúng, chưa tự đối chiếu cột CSV. Accountant tăng lên13 ID đã qua executor tại các checkpoint, không phải tỷ lệ model đạt. Không sửa runtime; review độc lập không thấy lỗi cần sửa.

TEC-011 đã có [đề xuất liên kết callout19 với dòng19](../reports/rag-14-departments-20261005/technical-callout19-20261015/README.md) qua hai crop trực tiếp từ PDF đúng hash. Đọc hình và đối chiếu độc lập hỗ trợ mã9.3.02414/vật liệuInox304/số lượng8 từ BOM. Đây là reference do agent đề xuất chờ nghiệp vụ duyệt, chưa nhập runtime và chưa chứng minh tự nhận diện callout; không sửa oracle cũ hoặc tăng số câu đạt. Draft150/1050, nghiệm thu0/14 và trạng thái tính năng giữ nguyên.

## 12. Checkpoint ngày 16/10/2026 — duyệt ký hiệu–dòng BOM

Đã nối [đường cấp quyền Math v2 so sánh](../reports/rag-14-departments-20261005/math-v2-release-wiring-20261016/README.md) bằng bundle/ledger ký riêng tới App/RAG runtime, REST/SSE và UI standalone. Kiểm pin sau ACL, bốn bước và rollback; không dùng grant v1 để mở v2 hoặc grant so sánh để mở BOM/Query/Graph. Nhóm90 backend và15 ca runtime cuối,35 UI đạt; build đạt. Đã nối tiếp [bộ chấm pilot v2](../reports/rag-14-departments-20261005/math-v2-pilot-20261016/README.md) vào lifecycle/gate hiện có, có contract và log hoàn tất riêng; giữ v1, không tính lại chứng cứ cũ. Đề xuất v2 bỏ provider smoke cho phép so sánh không gọi model. Đã bổ sung formal producer, review và sửa việc mượn report đạt giữa các câu; gate tái kiểm actual/reference/actor/citation/answer và budget, giữ zero-denominator/blocked theo reference. SQL staging mới đã backup/VERIFYONLY và áp V0043–V0047; phép so sánh synthetic 1500/1800/300/20 qua SQL Server thật đạt, thu hồi nguồn chặn tổng/citation cũ. Chỉ kiểm phần liên quan, không full suite hoặc gọi model. Chưa có commit sạch, formal pair phát hành, browser/API staging, nguồn/người ký/nghiệm thu thật hoặc activation; contract v2 chưa được duyệt cho live và chưa có cửa sổ mới. Đây là đường phát hành cục bộ; tiếp theo chốt candidate và các bằng chứng còn thiếu, không mở thêm fixture ngoài nhu cầu phát hành. Các mục migration “chưa áp staging” tại checkpoint thành phần phía dưới là trạng thái trước DB test mới này; việc áp trên DB synthetic không nghiệm thu các nguồn nghiệp vụ tương ứng.

[ACC-033](../reports/rag-14-departments-20261005/accountant-bom-execution-20261016/README.md) đã chạy nguyên câu qua executor với nguồn đóng trước chạy:51 occurrence cho306, context50; thiếu manifest/vượt500/mất quyền chặn tổng. Bốn biến thể đạt, review không có finding. Accountant14 ID qua executor tại các checkpoint, không phải14 câu model hoặc nghiệp vụ đạt. ACC-032 vẫn chưa chạy vì thiếu binding có kiểu và đã duyệt giữa số lượng lệnh sản xuất với đúng cụm gốc; không dùng literal chép từ file để ghi đạt.

Đã sửa [ô Excel định dạng ngày/giờ bị coi là số tiền](../reports/rag-14-departments-20261005/xlsx-cell-type-20261016/README.md) ở parser chung, gồm format nhiều phần và locale; giữ raw/Decimal, đưa vào xung đột cần xác minh. UI không điền sẵn giá trị của dòng xung đột để tránh bỏ qua bước nhập giá trị đã kiểm tra. 206 backend và26 UI kiểm tra liên quan đạt; helper mới coverage100%, component statement92,02%/branch89,92%; build và review cuối đạt. Guard áp dụng ingestion mới, không tự thay snapshot đã duyệt hoặc tạo nghiệm thu. Cần quy trình tái nhập/duyệt nguồn cũ riêng nếu áp dụng thay đổi.

Đã bổ sung [kiểm chứng đồng thời tổ hợp Graph–Query–Math](../reports/rag-14-departments-20261005/v2-concurrency-20261016/README.md) trên SQLite tạm: 1/5 request, mỗi mức có nhóm đủ quyền và nhóm có người ngoài quyền; tổng12 request,10 request hợp lệ thực thi50 bước Math đúng. Barrier ghi nhận5 request chồng lấn tại lần đọc đầu với5 connection và5 ledger riêng, không rò nguồn ở hai request bị từ chối. 80 kiểm tra liên quan đạt, review không có finding. Không sửa runtime hoặc gọi provider; đây chưa là REST/SSE concurrency, SQL Server/tải thực, ma trận baseline/candidate hoặc gate live.

[Lượt Luna V4 đã đóng](../reports/rag-14-departments-20261005/progress-technical-luna-v4-20261016.md): dùng đủ bảy lần gọi mới được duyệt, không retry, routing4/7; hai Math record/năm bước thực thi đúng nguồn. Cả bảy policy đã xác minh đóng, không tái sử dụng. TEC-028 vẫn unsupported; TEC-054 vẫn gọi planner; TEC-045/059 chưa thực thi Math. Đã sửa sau lượt đo [completion lỗi bị audit thành success](../reports/rag-14-departments-20261005/provider-finish-error-20261016/README.md) tại adapter chung, đạt83 kiểm tra tập trung và coverage100% hàm thay đổi. Chưa đo model sau sửa, không thay điểm V4 hoặc bằng chứng lịch sử. Draft150/1050 và nghiệm thu0/14 không đổi.

Đã hoàn thiện [backend/API/UI duyệt liên kết BOM](../reports/rag-14-departments-20261005/bom-callout-review-20261016/README.md), gồm phân trang, nguồn hiện hành, quyền hai đầu, fingerprint và vô hiệu hóa khi dòng đích thay đổi. Tái sử dụng dữ liệu/luồng duyệt hiện có, không thêm migration/dependency. 309 kiểm tra tích hợp backend, 123 kiểm tra tập trung cuối, 23 kiểm tra UI và ba luồng Chromium giả lập đạt; build đạt. Tự nhận diện ký hiệu đồ họa, SQL Server/staging và nghiệm thu vẫn thiếu. Đây là tiến triển năng lực đọc/duyệt chứng cứ, không tăng số câu chatbot đạt, không bật tính năng hoặc tạo acceptance. Draft150/1050 và nghiệm thu0/14 giữ nguyên.

Đã sửa [liên kết chi tiết–bản vẽ tự nhận snapshot mới](../reports/rag-14-departments-20261005/graph-binding-source-pin-20261016/README.md): proposal/approval/serving giữ pin nguồn do server chụp; liên kết cũ thiếu pin cần proposal mới được duyệt, không backfill acceptance. Đồng nhất chứng cứ quote BOM ở duyệt và truy xuất. 130 kiểm tra tập trung và 156 kiểm tra ảnh hưởng đạt, review độc lập không có finding. Migration V0047 chỉ được chuẩn bị, chưa áp staging/live; model, GUI binding và nghiệm thu vẫn còn việc.

Đã hoàn thiện [luồng đề xuất/duyệt liên kết chi tiết–bản vẽ trên quản trị](../reports/rag-14-departments-20261005/drawing-binding-review-20261016/README.md), có lối mở từ tài liệu hiện hành, danh mục đích có quyền, phạm vi tường minh và phân trang theo tài liệu nguồn. Proposal pending và quyết định duyệt được tách; response cũ/lỗi nguồn không tạo success. 113 backend, 57 UI và một luồng Chromium giả lập đạt; build và review độc lập đạt. Chưa áp V0047/staging, browser/backend thật hoặc tạo occurrence tự động; không tăng số câu nghiệm thu hoặc bật tính năng.


## 13. Checkpoint ngày 17/10/2026 — đường phát hành Graph v2

Đã bổ sung [Graph v2 có grant ký riêng qua App/RAG, REST/SSE và UI](../reports/rag-14-departments-20261005/graph-v2-release-wiring-20261017/README.md), tái sử dụng SQL/traversal/citation hiện hữu. Query được chặn trước SQL khi ngoài context cấp quyền; kết quả native sau ACL/snapshot phải khớp pin, kiểm lại grant trước render. Giữ8/500/50, partial/unknown và chặn enrichment Graph cũ khi chọn bundle mới. Không thêm Graph database/planner/model.

Candidate chưa commit/triển khai;193kiểm tra backend liên quan,37UI và typecheck đạt, mỗi file logic backend thay đổi đạt coverage>=80%, review agent không còn finding cao/vừa. Không chạy lại full suite/build, không gọi provider hoặc ký Graph bằng key thật. SQLite fixture không thay SQL Server/native gate/nguồn nghiệp vụ.

[Dự thảo contract](../reports/rag-14-departments-20261005/graph-v2-release-wiring-20261017/contract-draft.md) chưa được người dùng duyệt. Grant đầu tiên khóa exact query/result theo ACL; biến thể chưa được duyệt bị chặn, không tự coi đã triển khai cho14phòng. Bản chính vẫnf58d2e1 với Math hai nguồn QA đang bật; health kiểm lại hợp lệ. Ba bảng Graph v2 chính đều0dòng, chưa có occurrence/declaration/binding được duyệt. Còn cần nguồn, commit/gate/contract và bằng chứng phát hành thật. Graph-only không tự thay Math hoặc bật tổ hợp; Math+Graph cần contract/evidence tổ hợp riêng. Toàn plan và nghiệm thu14phòng vẫn chưa hoàn thành.


## 14. Checkpoint ngày 17/10/2026 — Math và Graph cùng runtime

Đã nối [quyền phát hành Math+Graph riêng](../reports/rag-14-departments-20261005/math-graph-v2-release-wiring-20261017/README.md), tái sử dụng hai bundle con có chữ ký cùng commit/version; parent phải có gate tích hợp riêng, snapshot khóa hai fingerprint và expiry lấy hạn sớm nhất. Không nới validator Math cũ hoặc tự kết hợp bằng hai flag. App/RAG/UI cho chọn một tác vụ Graph hoặc so sánh Math, chưa phải Query/BOM trong production.

Signed fixture qua SQLite/API/REST/SSE đổi Graph→Math→Graph đúng nguồn, thu hồi grant chặn cả hai409.114kiểm tra backend liên quan và39UI đạt; typecheck đạt; module tổ hợp mới88,57%statement, các đường executable bổ sung100%. Không full suite/build/model calls. Root tự kiểm tổ hợp; không ghi review agent chưa hoàn tất thành review độc lập. Candidate/source pins mới tách khỏi bằng chứng Graph-only cũ.

[Dự thảo contract tổ hợp](../reports/rag-14-departments-20261005/math-graph-v2-release-wiring-20261017/contract-draft.md) chưa được duyệt, chưa ký key thật hoặc commit/triển khai. Health bản chính vẫn Math QA hai nguồn trênf58d2e1, không sửa grant/config/source chính. Còn nguồn Graph duyệt/native gate, commit và quyền phát hành mới; Qdrant blocker cũ không tự được xóa. Query, BOM live, các capability còn lại và nghiệm thu14phòng vẫn chưa hoàn thành.

## 15. Checkpoint ngày 17/10/2026 — chứng cứ bản vẽ và người duyệt trên bản chính

Đã đưa [chứng cứ bốn bản vẽ hiện hành](../reports/rag-14-departments-20261005/primary-deployment-20261017/drawing-pending-evidence/README.md) Doc59/60/68/69 vào SQL chính, đối chiếu đúng file/hash/revision 3: 21 trường số lượng, 42 trường mã/vật liệu và bốn scope, tất cả pending. Có 14 số lượng thiếu và một mâu thuẫn Inox 201/304 ở Doc60/C2. Không suy cây sản phẩm hoặc lấy dữ liệu thiếu để tính; bốn bản này không đại diện toàn bộ 47 PDF hay cây hoàn chỉnh.

Theo xác nhận mới của người dùng, đã gán `admin_bao` (UserID81) thay tài khoản mẫu37 làm người duyệt đúng bốn bản, ghi bốn audit cùng transaction. Chỉ đổi KnowledgeApproverUserID; không đổi các cột tài liệu khác, vai trò, người phụ trách hoặc phê duyệt trường dữ liệu. HTTP App chính xác nhận tài khoản này đọc được các trường pending qua luồng duyệt hiện hữu; cả bốn chưa có scope đã duyệt để tính. Không tạo human acceptance thay người dùng.

Grant Math hai nguồn QA trên bản chính vẫn hợp lệ, Graph/Query vẫn tắt. Không restart, full suite, model call hoặc browser UI trong checkpoint này. Còn cần người duyệt đối chiếu số lượng/vật liệu, xác nhận phạm vi và quan hệ trước bước phát hành Graph; nghiệm thu 14 phòng vẫn chưa hoàn thành. Bằng chứng trước gán người duyệt giữ nguyên, kết quả gán và kiểm HTTP ghi riêng.



## 16. Checkpoint ngày 17/10/2026 — duyệt số lượng từ ảnh bản vẽ

Đã đối chiếu ảnh bảng kê của Doc59/60/68/69 và chuẩn bị [21 giá trị đề xuất kèm vùng nguồn](../reports/rag-14-departments-20261005/primary-deployment-20261017/drawing-pending-evidence/quantity-review-proposal.md). 14 ô trống ở lớp chữ có số lượng nhìn thấy trên ảnh; mẫu kiểm có nét vector và không có ký tự trong ô. Không coi lớp chữ trống là nguồn không có số lượng, không thêm OCR service hoặc gọi provider để đọc lại nguồn này.

Người dùng đã xác nhận “Duyệt 21 số lượng và đơn vị cái”. Đã ghi đúng 21 numeric fact qua review hiện hữu dưới admin_bao, tạo 21 audit, giữ PayloadJson/raw_value gốc và ghi ReviewedPayloadJson riêng. HTTP App chính trả đủ giá trị đã duyệt; bốn scope chưa được xác nhận đầy đủ nên load_approved_scope vẫn không trả nguồn tính được. Quyết định chỉ áp dụng số lượng/đơn vị; 42 trường mã/vật liệu vẫn pending, riêng Inox 201/304 ở Doc60/C2 chưa được giải quyết. Math QA vẫn bật và grant hợp lệ; chưa bật Graph hoặc tạo nghiệm thu phòng ban.

Kiểm nguồn Graph trên SQL chính thấy 42 dòng BOM cũ của bốn tài liệu, không có cạnh Graph tương ứng; raw legacy chưa có định danh dòng theo contract mới. Không tự dedup theo mã, thêm bản sao vào dữ liệu đang phục vụ hoặc sửa/xóa lịch sử để làm gate đạt. Còn cần nối bộ chứng cứ dòng đã đóng với luồng tạo/duyệt cạnh, occurrence và khai báo đầy đủ; bản vẽ con chưa xác định phải giữ unresolved. Không cộng các bảng kê công đoạn thành một cây sản phẩm.

Checkpoint chỉ dùng kiểm SQL/API và ảnh nguồn, không full suite/build/model calls/browser. Đây là tiến triển dữ liệu và quyết định số lượng thật trên bản chính, không phải bằng chứng hoàn thành Graph hoặc toàn plan 14 phòng.


## 17. Checkpoint ngày 17/10/2026 — cạnh đã duyệt và chứng cứ occurrence legacy

Đã bổ sung [cầu nối chứng cứ dòng BOM cũ](../reports/rag-14-departments-20261005/graph-legacy-row-proof-20261017/README.md) ở candidate, dùng chung khi đề xuất occurrence và đọc Graph v2. Native source_row_id giữ nguyên. Nhánh legacy chỉ nhận proposal cạnh đã duyệt, đúng document/version/page/part, source quote BOM gốc, fact ID/dòng, snapshot/hash hiện hành và toàn payload numeric đã duyệt; BIN2/DATALENGTH chặn thay payload kể cả hoa/thường/khoảng trắng cuối. Không gộp mã hoặc thêm/sửa/xóa BOM lịch sử. Payload hơn4.000ký tự bị chặn; một row legacy cho mỗi edge proposal, các occurrence lặp/khác trang tiếp tục dùng chứng cứ native.

RED đã tái hiện trước sửa,99kiểm tra Graph liên quan đạt,15ca mới đo riêng coverage; executable logic bổ sung100%, không suy coverage nhánh SQL từ Python hoặc dùng coverage tổng che phần thay đổi. Một review agent chỉ đọc không thấy lỗi cụ thể. SQL Server chỉ đọc kiểm21pending thật khớp0, projection approved khớp21, projection sửa payload khớp0; các projection không tạo human acceptance hoặc gate serving. Lỗi collection của lượt coverage đầu được giữ riêng, xử lý bằng preload native dependency, không sửa sản phẩm hoặc bỏ ca lỗi.

Trên bản chính, đã chuẩn bị22node định danh và21đề xuất CONTAINS_PART; người dùng sau đó xác nhận duyệt đúng21liên kết bản vẽ có dòng BOM mã chi tiết. API chính/CSRF ghi21cạnh approved dưới admin_bao với audit. Không coi node dùng chung là khử trùng occurrence. Operator candidate dùng chứng cứ và cạnh đã duyệt tạo21occurrence cùng4declaration pending, không duyệt per_parent/is_leaf/binding/đầy đủ cây. Product scope lấy mã thành phẩm được ghi trong nguồn; mỗi root vẫn là từng DocID, không gộp bảng kê công đoạn của cùng sản phẩm thành cây. API quản trị bản chính thấy đủ11/10occurrence và2/2declaration ở hai scope.

Code mới chưa commit/triển khai, contract Graph mở rộng chưa được duyệt. Main runtime vẫn f58d2e1, chỉ Math hai nguồn QA bật và grant hợp lệ. Bốn scope numeric và42trường chữ vẫn pending, Inox201/304 chưa giải quyết, chưa có binding bản vẽ con. Còn cần review occurrence/declaration, mã phát hành/gate/bundle và bằng chứng rollout tương ứng; không dùng21cạnh approved thay nghiệm thu14phòng. Không chạy full suite/build/browser/provider hoặc ký Graph trong checkpoint này.


## 18. Checkpoint ngày 17/10/2026 — native Graph đã có nguồn duyệt và commit candidate

Người dùng đã duyệt đúng21occurrence và4declaration hiện có qua admin_bao. Đã ghi25audit, giữ per_parent là một lần áp dụng bảng kê từng bản vẽ, rows_complete/tree_complete=False; không xác nhận lá, bản vẽ con hoặc phạm vi toàn cây. [Kết quả nguồn](../reports/rag-14-departments-20261005/primary-deployment-20261017/drawing-pending-evidence/graph-v2-record-review-result.json) khác với nghiệm thu14phòng. Bốn scope numeric và42trường chữ vẫn pending, xung đột vật liệu chưa giải quyết.

Đã đóng trước khi đọc [22query native](../reports/rag-14-departments-20261005/graph-primary-native-20261017/README.md), gồm4contains và18where_used, với kỳ vọng từ payload được người dùng duyệt. SQL chính trong SERIALIZABLE trả22/22khớp cạnh/giá trị/nguồn và partial; không tạo grant hoặc gắn nhãn formal. Một review agent tập trung Math+Graph v4 không có finding, không lặp lại review Graph/cầu nối đã hoàn tất.

Kiểm luồng Graph/BOM/transport còn thiếu đã bắt lỗi fixture SQLite chưa dịch DATALENGTH/BIN2 ở connection được sao chép. Đã sửa đúng adapter test dùng chung, không thay runtime để né lỗi:103kiểm tra ảnh hưởng đạt, không lỗi/skip. Source pins và99kiểm tra ở checkpoint trước giữ ý nghĩa lịch sử; không tự coi chúng chạy trên fixture mới. Không mở full suite/build/model.

Theo xác nhận mới, đã commit local đúng27tệp ở **bf7832bc0b3edf757cdd0d4ee6245bac39d6016d**, cha f58d2e1. [Receipt và patch](../reports/rag-14-departments-20261005/graph-math-release-preparation-20261017/commit-receipt.json) kiểm đúng phạm vi, canonical ECC secret hook chạy nguyên trạng qua launcher Git Bash, không bypass hoặc đổi config Git lâu dài. WIP .gitignore/plan/coverage ngoài phạm vi được giữ. Dependency không đổi; audit cảnh báo trước vẫn mở. Không push hoặc triển khai lên bản chính.

Trên commit mới, ba request qua executor evaluation đọc SQL chính theo thứ tự Graph chứa→Math→Graph truy ngược đạt đúng nguồn; Math vẫn1500/1800/300/20% và4bước, Graph giữpartial;0planner/0final generation. Đây chưa là signed production HTTP/SSE hoặc formal combination gate. Main runtime vẫnf58d2e1 và Math QA hiện hữu; Graph/Query giữtắt.

[Đề xuất contract Graph và Math+Graph](../reports/rag-14-departments-20261005/graph-math-release-preparation-20261017/contract-review-proposal.md) đang chờ người dùng duyệt, không hạ ngưỡng hoặc xóa blocker Qdrant. Gate vẫn cần baseline/candidate đúng phạm vi, tăng chất lượng10điểm%, precision95%, structured coverage80%, provenance100%, latency/cost1,5 và các điều kiện domain/safety hiện hữu. Inventory chỉ đọc thấy Production/Maintenance có nguồn hiện hành nhưng chưa có BOM trong bảng nguồn; bốn bản vẽ đang duyệt chỉ được chia sẻ Technical. Nguồn QA kỹ thuật ngoài danh sách14phòng không được tính thành phòng nghiệp vụ thứ15.

Đã đối chiếu khung tên13bản vẽ hiện hành, không suy từ Model: mã8.3.05520.265 ở Doc59 trùng MãBTP của Doc58; mã8.1.06523.019 ở Doc69 trùng MãBTP của Doc65, đúng product scope. [Hai binding có ảnh/chứng cứ](../reports/rag-14-departments-20261005/primary-deployment-20261017/drawing-pending-evidence/drawing-child-link-review-proposal.md) còn chờ duyệt, chưa đổi người duyệt hoặc snapshot của hai tài liệu đích. Các mã hàn/nhám/sơn khác nhau không tự nối thành cùng chi tiết hay quan hệ chứa. Toàn plan vẫn chưa hoàn thành.


## 19. Checkpoint ngày 17/10/2026 — Accountant thêm bốn câu có bằng chứng thực thi

Đã chạy nguyên ACC-024/025 qua executor và CSV/SQLite đóng trước thực thi: sổ quỹ100/95/−5/−5%, phải thu1.400/1.450/+50/+3,57%, giữ dòng điều chỉnh âm. Hai biến thể khác cutoff/customer bị chặn, không bước tính hoặc citation; tổng bốn request và8bước Math đúng kỳ vọng. [Nguồn và kết quả](../reports/rag-14-departments-20261005/accountant-balance-comparison-20261017/README.md) giữ bank/source/script/snapshot hash, revision riêng2/7. Định nghĩa scope do fixture reviewer khai báo; chưa tự matching các cột/giao dịch và chưa chứng minh model chọn nguồn. Lỗi pin đường dẫn ở lần chuẩn bị đầu được giữ riêng, dừng trước executor; gói mới không ghi đè bằng chứng cũ.

[ACC-073/074](../reports/rag-14-departments-20261005/accountant-query-overflow-20261017/README.md) giữ nguyên câu hỏi: rõ bốn việc chặn với0planner; câu mơ hồ dùng1planner giả lập trả overflow rồi chặn. Listener xác nhận cả hai không đọc SQL nguồn, không subquery/Math/citation/final generation. Chưa chứng minh model thật phân loại ACC-074. Không sửa runtime/contract hoặc gọi provider; không chạy lại full suite/build/browser. Các script tái lập chạy từ candidate worktree, bản sao report trên checkout chính dùng để đọc chứng cứ.

Accountant hiện **19/75 ID đã qua executor** tại các checkpoint: ACC-011–020,024–025,032,033,040,071–074; không phải19câu model hoặc nghiệp vụ đạt. ACC-032 đã có binding và luồng SQL/browser tại checkpoint BOM, thay cho thông tin chưa chạy ở các ghi nhận cũ. Nhóm ACC-001–010 vẫn thiếu lựa chọn một scope có cấu trúc để tính tổng; không thay tổng bằng so sánh với0 hoặc cộng top-k. Draft vẫn150/1.050, nghiệm thu0/14. Main HEAD giữf58d2e1, candidatebf7832b, product paths sạch; không commit thêm/push/restart/ký hoặc bật tính năng. App chính `/api/health` trảok khi kiểm; probe RAG thiếu service token trả401, không được coi là health thành công của RAG.


## 20. Checkpoint ngày 17/10/2026 — nguồn tính tổng kế toán và khoảng trống contract

Đã đóng trước xử lý [mười nguồn CSV và oracle ACC-001–010](../reports/rag-14-departments-20261005/accountant-total-source-preparation-20261017/README.md). Bộ đọc/repository hiện có giữ38dòng gốc, fixture reviewer chọn21dòng và khai báo17dòng loại có lý do; mười scope trả đúng tập nguồn và tổng Decimal theo literal, giữ điều chỉnh âm và số dư thay doanh số phát sinh. File/script/helper/bank/SQLite hash khớp; không sửa bank, source code hoặc dữ liệu chính. Đây là chuẩn bị nguồn và kiểm repository, chưa chạy mười câu qua executor/model, Accountant vẫn19/75ID và nghiệm thu0/14.

Kiểm call sites xác nhận Math v1 đang nối dữ liệu SQL BOM, còn hai mẫu Math v2 không có tổng một scope bảng kế toán. Đã chuẩn bị [đề xuất mở rộng phạm vi](../reports/rag-14-departments-20261005/accountant-total-source-preparation-20261017/scope-total-contract-proposal.md) bằng một mẫu sum cố định, đọc đủ manifest đã duyệt và giữ quyền/phiên bản/làm tròn/citation; đang chờ người dùng quyết định vì khác giới hạn hai mẫu trong plan hiện hành. Chưa triển khai hoặc đổi contract/gate, không dùng so sánh với0 hay giả trang PDF cho CSV. Quyền đồng ý đề xuất không tự cấp commit/API có phí/chữ ký/activation.

Đã đọc lại chẩn đoán Qdrant HTTP ngày15/10: lượt đó vẫn INCOMPLETE vì snapshot cuối ReadTimeout; phần nhận body chậm chưa xác định được nguyên nhân mạng/proxy/server. Không mở thêm vòng đo khi chưa có giả thuyết/phép kiểm cụ thể, không sửa timeout/retry hoặc xóa blocker. Không full suite/build/browser/model, không commit/push/restart/ký/bật tính năng trong checkpoint này. Bản chínhf58d2e1/candidatebf7832b và các phê duyệt phát hành còn chờ giữ nguyên.


## 21. Checkpoint ngày 17/10/2026 — thấy toàn bộ dòng nguồn khi duyệt số

Đã bổ sung ngữ cảnh dòng gốc Excel/CSV ngay trong màn hình duyệt hiện có, ghép đúng tài liệu/phiên bản/hash/bảng/sheet/dòng và phân trang. Kiểm quyền reviewer trước đọc; cùng bản sao giới hạn dung lượng được kiểm hash rồi phân tích, tệp đổi/mất/ngoài vùng nguồn không tạo ngữ cảnh. Văn bản ô hiển thị thuần, không thực thi HTML; thông tin gốc chưa được coi là dữ liệu đã duyệt. Không đổi manifest/hash lịch sử, schema SQL hay dependency.

[Diff, pin và kết quả](../reports/rag-14-departments-20261005/source-row-review-context-20261017/README.md):58kiểm backend/API và24component đạt, typecheck đạt;10ca mới thuộc58ca trên đo helper mới100%statement/branch. Review tập trung không còn finding; không full suite/build/browser/model. Chín tệp candidate chưa commit/deploy, bản chính giữ nguyên. Giới hạn: đọc lại file mỗi trang và chưa có matching nghiệp vụ đa cột; không thay số Accountant19/75ID,150/1.050draft hoặc nghiệm thu0/14.


## 22. Checkpoint ngày 19/10/2026 — Accountant thêm ba câu về phạm vi và quyền

[ACC-058/062/066](../reports/rag-14-departments-20261005/accountant-scope-permissions-20261019/README.md) giữ nguyên câu hỏi, đóng nguồn/oracle/pin trước thực thi. Năm request qua executor: khác cơ sở thuế và thiếu quyền bị chặn không tính/citation hoặc lộ định danh nguồn qua events; quyền hai site và nguồn Purchasing được share Accountant trả đúng100/120/20/20% và200/250/50/25%, tám bước riêng. Pin bank/source/SQLite/product files khớp trước-sau.0model/planner/final generation, chưa chứng minh semantic routing/production HTTP/SSE/nghiệp vụ; cơ sở thuế do fixture reviewer khai báo.

Lượt đầu timeout tại checkpoint scope_comparison trước SQL, chưa hoàn thành ca nào; giữ riêng lỗi. Cùng source package chạy warm sau khi nạp module đạt5/5, không đổi deadline. Import riêng7,469giây chưa giải thích nguyên nhân lần đầu; không dùng warm để đóng blocker khởi động hoặc gate latency. Không lặp full suite/build/browser/API có phí, không sửa production.

Accountant hiện22/75ID qua executor ở các checkpoint: ACC-011–020,024–025,032,033,040,058,062,066,071–074. Không phải22câu model hoặc nghiệp vụ đạt. Draft150/1.050, nghiệm thu0/14; contract tổng một scope và Graph/Math+Graph cùng các binding nguồn vẫn chờ quyết định. Chín tệp ngữ cảnh dòng nguồn của checkpoint21 chưa commit/deploy; không phát sinh delta runtime khác trong lượt này.


## 23. Checkpoint ngày 19/10/2026 — chốt mã duyệt dòng nguồn theo phê duyệt

Người dùng đã cho phép commit local đúng9tệp của phần ngữ cảnh dòng nguồn. [Patch/manifest/phê duyệt/receipt](../reports/rag-14-departments-20261005/source-row-review-context-20261017/governance-20261019/README.md) có đủ7tệp sửa và2tệp mới; patch tracked-only cũ không dùng làm phạm vi duyệt. Đã chốt ở 65f6b271d63358863dc1a136d8b9661d365bf070, cha bf7832b, đúng9tệp và product paths sạch. Hash byte khớp bản đã kiểm/review; canonical ECC pre-commit chạy qua Git Bash, không bỏ hook hoặc đổi config Git lâu dài. WIP .gitignore/plan/coverage ngoài phạm vi được giữ.

Tái sử dụng58kiểm backend/API,24component và typecheck trên đúng mã; không mở full suite/build/browser/model.280phiên bản Python vẫn khớp audit17/10; dependency files không đổi, cảnh báo audit cũ tiếp tục mở và không coi là security gate toàn ứng dụng đã đạt. Chỉ commit local: không push/triển khai/ký/bật flag. Main HEAD vẫn f58d2e1 khi kiểm; đây là kiểm Git, không xác nhận health hoặc activation runtime ngày19/10.

Accountant giữ22/75ID qua executor, Technical42/75ID tại checkpoint riêng; tổng64ID có bằng chứng selection/fixture lịch sử, không phải64câu model/nghiệp vụ đạt. Draft150/1.050, nghiệm thu0/14. Các contract tổng một scope, Graph/Math+Graph và hai binding bản vẽ đang chờ quyết định không được suy ra từ quyền commit này. Lỗi timeout lần đầu ở checkpoint22 vẫn chưa xác định nguyên nhân.


## 24. Checkpoint ngày19/10/2026 — khoanh vùng timeout và giữ nguồn tổng đã đóng

Đã quan sát một request đầu không nạp sẵn module, giữ deadline120giây trên candidate65f6b271 và package ACC-058 cũ: [kết quả](../reports/rag-14-departments-20261005/accountant-scope-permissions-20261019/cold-probe-65f6b271/README.md) chạy6.985ms,21SQL statement, chặn đúng khác cơ sở thuế; không tái hiện timeout. Không sửa runtime theo suy đoán, không coi lỗi khởi động đã được khắc phục hoặc latency gate đạt; không lặp lại năm ca đã có. Chưa có tác nhân cụ thể để mở thêm vòng đo lỗi này.

[Đối chiếu nguồn tổng ngày19/10](../reports/rag-14-departments-20261005/accountant-total-source-preparation-20261017/source-pin-recheck-20261019.json) xác nhận15pin của10CSV, input/snapshot/bank/script/helper giữ nguyên. Không chạy lại chuẩn bị nguồn, không thay câu hỏi/oracle, không triển khai mẫu sum khi contract còn chờ quyết định. Accountant22/75ID và nghiệm thu0/14 giữ nguyên.


## 25. Xác nhận phạm vi ngày19/10/2026 — tổng một scope

Người dùng đã [duyệt đề xuất](../reports/rag-14-departments-20261005/accountant-total-source-preparation-20261017/contract-user-approval-20261019.json) bổ sung `sum_reviewed_scope`, chỉ cho triển khai mã. Mục Grounded Math đã cập nhật mẫu thứ ba với schema/contract đánh giá riêng; giới hạn và bằng chứng so sánh/BOM cũ giữ nguyên. Khởi đầu là request chọn một scope rõ ràng trong evaluation, chưa thêm planner hoặc nhánh Query mới; production vẫn chặn mẫu này. Không tự duyệt nguồn thật hoặc bật live từ xác nhận phạm vi.


## 26. Checkpoint ngày19/10/2026 — tổng một scope đã nối đủ đường sử dụng

Đã triển khai [mẫu tổng độc lập](../reports/rag-14-departments-20261005/scope-total-implementation-20261019/README.md) theo contract được duyệt ở mục25. Đọc đủ manifest đã duyệt trong snapshot, kiểm quyền/revision/currentness, Decimal một bước, làm tròn sau tổng; phạm vi rỗng cần chứng cứ rỗng. Schema/đánh giá mới tách riêng, không tái sử dụng gate hoặc grant hai mẫu cũ. Production/pilot và planner contract đều bị chặn; chưa mở QueryBranch cho mẫu tổng.

Nối engine/executor, REST/SSE, adapter, lựa chọn một scope trên UI, công thức/giá trị trước-sau làm tròn và nguồn phân trang. Lịch sử mẫu mới lưu SourceVersion cùng pin và dựng lại citation sau kiểm quyền; sửa thiếu SourceVersion ở đúng nhánh này. Mất quyền site, đổi revision hoặc thu hồi fact đều ẩn kết quả. Luồng comparison/BOM cũ giữ contract hiện hữu.

Mười câu ACC-001–010 nguyên văn nay qua executor trên10CSV/SQLite đã đóng17/10, không sửa nguồn/oracle:10/10đúng literal và evaluator riêng,10bước;21dòng chọn/17dòng loại có lý do được bảo toàn.0planner/model, không giả nguồn so sánh0. Đây là fixture reviewer và explicit selection; chưa tự lọc/diễn giải nghiệp vụ, chưa là model hoặc nghiệm thu thật. Accountant tăng22→32/75ID, Technical42/75tại checkpoint riêng, tổng74ID thực thi lịch sử; draft150/1.050 và nghiệm thu0/14.

Kiểm phần ảnh hưởng:127core,22transport và4history/capability đạt;12component vàtypecheck đạt; một browser fixture chọn/gửi/hiện đúng tổng và citation. Các lượt coverage có giao với các test trên, không cộng thành câu hỏi hay bộ kiểm mới. Python statements thay đổi92,62%, từng module thay đổi>=80%; logic mới hai component100%. Review tập trung toàn delta không còn finding. Browser lần đầu trắng do fixture route bắt nhầm /src/api khi chạy Vite; chỉ sửa fixture và chạy lại đúng một ca đạt1,4giây, giữ capture cũ. Browser fixture chưa thay đường browser nối SQL thật/production.

Candidate [30tệp và patch](../reports/rag-14-departments-20261005/scope-total-implementation-20261019/implementation-result.json) từ65f6b271 chưa commit; không dependency/migration/provider/push/ký/activation hoặc đổi runtime chính. Vite kiểm riêng đã đóng. Full plan và ma trận tích hợp/phát hành của mẫu mới vẫn chưa hoàn thành; quyền triển khai mẫu không tự cho commit/live hoặc human acceptance.


## 27. Checkpoint ngày19/10/2026 — tổng nhiều ý và lỗi phân loại cần sửa

Đã nối tổng một scope vào nhánh Query chọn rõ ràng, tối đa3ý với nguồn/kết quả riêng, deadline/ngân sách chung và schema `query-plan-scope-total-v1`. Một tổng giữ đường độc lập; production/planner contract cũ vẫn chặn. REST/SSE, adapter, UI và lịch sử kiểm đúng scope/hash từng nhánh; nguồn nhánh khác không thể thay thế.

[Bằng chứng phần chọn nhánh](../reports/rag-14-departments-20261005/scope-total-query-20261019/README.md):55kiểm core ảnh hưởng,8transport/history,21codec,14UI và typecheck đạt; một browser fixture hai tổng/max3 đạt, Vite tạm đã đóng. Coverage phần Python tăng thêm94,29%, từng module>=80%;37test đo coverage giao các test trên, không cộng thành câu nghiệp vụ. Các pin ở checkpoint này trước sửa semantic tiếp theo, không tự áp cho mã sau.

Bộ ba câu hỏi ACC-001/002/003 nguyên văn đã đóng trước chạy nhưng bị guard cũ đếm dấu phẩy thành5ý và từ chối nhầm trước SQL/Math. Giữ input/oracle/lỗi riêng, không sửa câu hỏi hoặc coi từ chối này là đúng. Chưa đạt kiểm kết hợp trên câu nghiệp vụ này. Hướng sửa là tái dùng bộ phân loại một lượt hiện có theo quy tắc Query đã được duyệt, binding scope riêng; không model tính số/chọn dòng, không API có phí hoặc đổi policy.

Accountant32/75ID, Technical42/75ID lịch sử,150/1.050draft và nghiệm thu0/14 giữ nguyên. Không full suite/build/commit/push/ký/migration/activation; live chính chưa được kiểm lại trong checkpoint này. Toàn plan còn mở.


## 28. Checkpoint ngày19/10/2026 — phân loại một lượt cho Query có tổng

Đã bổ sung opt-in nội bộ evaluation/test `semantic-scope-total-query-routing-v1-draft`, tái dùng bộ phân loại strict hiện hữu; parent/child binding pin đúngscope/question, không chọn nguồn/dòng hoặc tính số bằng model. Lệnh tổng đơn giản0classifier; câu rõ4tác vụ chặn trước classifier; overflow/phân loại sai/span/scope/thứ tự/deadline chặn trước chứng cứ. Contract/requesthash cũ khi không cósum giữ nguyên. REST/SSE mặc định chưa opt-in, không phát sinh provider call mới hoặc sửa policy.

[Ba kịch bản giữ nguyên câu ACC-001/002/003 và nguồn17/10](../reports/rag-14-departments-20261005/scope-total-query-20261019/classified-combinations/execution.json) nay đạt qua contract mới:1500/500/950; thiếu phạm vi giữa vẫn trả1500/950; nhánh thứ3phụ thuộc bị chặn và giữ1500. Đóng trước chạy câu/oracle/responsegiả lập/code/sourcepin;6bước tính thật,3classifierstub/0provider. Không sửa hoặc ghi đè bằng chứng guard từ chối cũ; chưa chứng minh model thật hoặc HTTP mặc định giải quyết được câu tự nhiên đó.

54/55kiểm routing ảnh hưởng đạt lần đầu; ca còn lại dùng nhầm3snapshot của cùngdoc khiến repository vô hiệu hóa đúng2scope cũ. Chỉ sửa fixture thành một snapshot chung và chạy lại đúngca đạt, productkhôngđổi. Coverage phần semantic mới95,83%statement, từngmodule>=80%; không fullsuite/build/browser/model lại. Candidate35tệp kèmfullpatch đã đóng để duyệt commit, chưastaging/commit/activation; cảnh báodependencycũ giữ nguyên.

Accountant32/75ID và Technical42/75ID lịch sử,150/1.050draft, nghiệm thu0/14 không tăng từ việc đo lại tổ hợp. Mẫu tổng/Query mới chưa phát hành; Graph/Qdrant và các nguồn/binding cần duyệt ở checkpoint trước vẫn chưađượcđóng. Toàn plan tiếp tục mở.


## 29. Checkpoint ngày19/10/2026 — chốt mã tổng và Query theo quyền đã duyệt

Người dùng đã cho phép commit local đúng35tệp mẫu tổng một phạm vi và Query kết hợp. Đã chốt **28fc5b557d9d6d9396e388981e21696a01dc69c6**, cha65f6b271. [Phê duyệt/receipt/fullpatch](../reports/rag-14-departments-20261005/scope-total-query-20261019/candidate-review/commit-receipt.json) kiểm đúng35tệp và hash byte đã review, productpaths sạch; WIP .gitignore/plan/coverage giữ ngoài commit. Canonical ECC hook chạy qua launcherGitBash, không bypass hoặc đổi config lâu dài.280dependencyPython vẫn khớp audit17/10; dependencyfiles không đổi, cảnh báo auditcũ còn mở.

Tái sử dụng bằng chứng đã đóng trên đúngbytes, không chạy lại test/build/model để commit. Không push/deploy/ký/migration/activation; mainHEAD vẫnf58d2e1 khi kiểmGit. HTTPmặcđịnh chưa opt-in classifier mới và semantic model/live chưa đạt; quyền commit không cho provider call hoặc human acceptance. Accountant32/75ID, Technical42/75ID lịch sử,150/1.050draft, nghiệm thu0/14 giữ nguyên; toànplan còn mở.


## 30. Điều chỉnh cách thực hiện theo yêu cầu người dùng ngày19/10/2026

Người dùng phản ánh tiến độ quá chậm: tập trung quá lâu vào Accountant/Technical, còn12phòng chưa bắt đầu. Từ checkpoint này phải ưu tiên kết quả sử dụng thực tế và đường phát hành, không tiếp tục tối ưu số lượng test hoặc hồ sơ riêng cho từng ca.

- **Phát hành từng tính năng khi đủ điều kiện.** Không chờ đủ1.050câu hoặc nghiệm thu toàn14phòng mới bật một phần đã đạt.75câu/phòng là bộ nghiệm thu của phòng đó; không tự biến thành prerequisite chung cho phát triển/phát hành độc lập. Chưa đủ nguồn/quyền/gate thì ghi đúng phần đang chặn, không tuyên bố đã sẵn sàng.
- **Tái sử dụng nền tảng cho các phòng.** Chỉ bổ sung nguồn, quyền và khác biệt nghiệp vụ của từng phòng; không tạo14pipeline,14bộ khung hoặc lặp toàn bộ kiểm nền tảng cho mỗi phòng. Ưu tiên ca đại diện có rủi ro và kiểm phần thay đổi; hoàn thiện bộ75câu theo đợt nghiệm thu tương ứng.
- **Dừng vòng kiểm mở rộng chưa phục vụ phát hành.** Sau một sửa đổi, chạy đúng nhóm ảnh hưởng và luồng quan trọng còn thiếu một lần; đã đạt và mã/nguồn/quyền không đổi thì tái sử dụng kết quả. Chỉ kiểm thêm khi có lỗi/đổi mã hoặc khoảng trống cụ thể. Không mở full suite/build/benchmark/review thêm chỉ để tăng chắc chắn chung chung.
- **Sửa tận nguyên nhân chung.** Không hardcodecaseID, đổi câu hỏi/oracle sau khi thấy kết quả, bỏ ca lỗi hoặc nới guard để đạt. Phát hiện lỗi ngoài đường phát hành hiện tại thì ghi lại ngắn gọn và xếp ưu tiên; không tự mở thêm chuỗi refactor, framework hoặc test độc lập.
- **Gộp các quyết định cần người dùng.** Hoàn thiện patch/phạm vi/bằng chứng cụ thể trước khi hỏi; gộp các thay đổi liên quan trong một đề xuất thay vì xin từng tệp hoặc từng bước. Tiếp tục phần độc lập khi chờ. Không suy quyền commit/ký/live/provider có phí ngoài phạm vi đã duyệt, không tái sử dụng cửa sổ API đã đóng.
- **Báo tiến độ bằng kết quả người dùng nhận được.** Nêu tính năng đang bật thực tế, nguồn/phạm vi dùng được, phần còn chặn và hành động tiếp theo. Tách đã chạy câu/đúng số/được model hiểu/nghiệm thu nghiệp vụ; không lấy số unit test hoặc fixture pass làm tỷ lệ hoàn thành14phòng. Không hứa thời gian hoàn tất khi chưa có cơ sở.
- **Giữ các kiểm tra bắt buộc về số học, nguồn/phiên bản, phân quyền, hủy/deadline và rollback.** Điều chỉnh này giảm công việc trùng lặp, không bỏ các bảo đảm hoặc tự hạ ngưỡng/gate đã chốt. CAD/3D vẫn để giai đoạn sau.

Lượt dữ liệu Accountant đang chạy đã dừng mở rộng: một lệnh hoàn tất trước khi nhận chỉ đạo,10request gồm8ID mới và2đối chứng; chỉ xác nhận guard/số, còn thiếu Excel filter/formula và hướng dẫn từ chối cụ thể. Giữ bằng chứng trong accountant-data-quality-execution-20261019, chưa coi8câu là nghiệm thu đầy đủ. CaACC-054 kiểm lineage đúng ô nhưng thiếu filename citation ở executor đã được ghi nhận, chưa sửa hoặc chạy lại. Không dùng các lượt này để trì hoãn đường phát hành tiếp theo.

## 31. Tiếp nhận bàn giao ngày19/10/2026 — quyền phát hành tổng một phạm vi

Đã tạo Goal bền mới với đầy đủ mục tiêu14phòng, không token budget; tiếp tục đúng candidate`28fc5b5`, main vẫn`f58d2e1`, giữ WIP. [Delta tổng một phạm vi](../reports/rag-14-departments-20261005/scope-total-release-wiring-20261019/README.md) nối bundlev5/contract/ledger riêng qua App, REST/SSE, executor, giao diện và lịch sử, kiểm lại grant/quyền/nguồn trong snapshot và giới hạn một bước. Grant so sánh không cấp quyền tổng hoặc ngược lại. Các kiểm ảnh hưởng và một review tập trung đạt; coverage phần thay đổi từng module>=80%, browser dùng API giả lập, không gọi model. Chưa commit, ký, bật hoặc ghi nghiệm thu.

Kiểm hiện trạng thật: App8080/RAG8100 không kết nối được; không tiếp tục tuyên bố Math đang phục vụ từ snapshot17/10. Cấu hình grant so sánh đã lưu vẫn hợp lệ; SQL chính và hai nguồn QA Doc1171v1/1172v2 vẫn đúng byte/hash/scope/quyền/tập dòng, tổng1500,00/1800,00VND. Kiểm nguồn chỉ đọc, không phải lượt chạy runtime hoặc formal. Strict profile helper từ chối grant archived/disabled/CHUNG nhưng đường profile thường lọc chúng theo thiết kế; không thêm gate hoặc nới quyền mới.

Đề nghị duyệt contract tổng và commit đúng20tệp trong receipt; chưa xin ký/bật khi thiếu formal và tích hợp cùng tồn tại với so sánh. Bundlev5 hiện chỉ tổng, không thay bundle so sánh rồi làm mất chức năng đã cấp quyền. Ngưỡng và bằng chứng tổng phải chốt riêng; không mượn gate cũ hoặc nhận fixture thành live readiness. Graph, Query, nguồn12phòng và nghiệm thu đủ14phòng vẫn còn trong Goal, không thay điều kiện hoàn thành.

Người dùng sau đó duyệt contract và commit local20tệp (sửa lựa chọn ban đầu thành số1); mã đã chốt ở`d7aee7fb26c7adcdacc9d8d76e17e1d193b01cf3`, hook bảo mật không bypass. Quyền20tệp đã dùng xong; WIP plan/handoff/gitignore/coverage ngoài commit giữ nguyên. Audit dependency mới khớp version/ID/advisory cũ, cảnh báo vẫn còn; review không thấy exposure mới do patch, không claim security gate sạch hoặc quyền bật. Bước tiếp theo là tích hợp tổng và so sánh cùng runtime, giữ grant/gate con riêng, không tạo14pipeline hoặc kiểm lại nền tảng không đổi.

## 32. Ngày19/10/2026 — tổng và so sánh cùng runtime, chuẩn bị gate tổng riêng

[Delta18tệp chưacommit](../reports/rag-14-departments-20261005/math-templates-release-wiring-20261019/README.md) trên`d7aee7f` đã nối bundlecha v6 với comparisonv2/totalv5 độc lập, cùng commit và hạn dùng sớm nhất; chọn capabilitycon trong guard/executor và kiểm lại parent khi đọc nguồn. Không thêm Settings/callback/service/DBschema/dependency. UI cho một tác vụ tổng hoặc so sánh; nguồn lọc theo từngtácvụ, không mượn quyền giữahai mẫu. Gatecha vẫn cần duyệt riêng, không tự bật hoặc kế thừa chữ ký cũ.

Mẫu tổng đã nối vào frozenrunner/exporter với schema và adapterformal riêng; bácặp phải độc lập theo frozenrun/case/trace, đúngoracle/answer/citation và cost/latencyghi nhận. MộtP2review về cloneobservation đã được tái hiện và sửa; kiểm phần thay đổi/coverage>=80%, component/typecheck và browser đổi tổng→so sánh đạt. Không chạy lại fullsuite hoặc model.

LượtSQLchính chỉ đọc đóng input/code/source/actor/oracle trướcchạy đã thực thi hai tổngQA1500,00/1800,00VND qua executor/exportermới; không provider, không ghiSQL, không khởi động App/RAG, không phảiformalpair hoặc nghiệmthu. Kiểm kêfolderchuẩn của14phòng cónguồn; chỉAccountant có2scopeQAđủdòng, cácphòngkhác chưa có scopehoànchỉnh để nhận tính tổng. Cần bổ sung nguồn/quyền/duyệt/nghiệpvụ và75câu/nghiệmthu từngphòng, tái dùngnền tảngchung.

Đề xuất gộp duyệt contractcha và commitđúng18tệp; chưa cóquyềnmới cho commit/ký/live/provider. Mục tiêu đủ14phòng, nguồn/quan hệ, các gate và nghiệmthu toànplan giữ nguyên; không tự ghiacceptance hoặc đổi nghĩafixture thànhlive. Cảnhbáodependency vàfilenamecitationexecutor cũ vẫn mở.

Đối chiếu xuất xứ nguồn trong folder chuẩn của 12 phòng còn lại: 51/60 tài liệu thuộc gói evaluation hoặc mang tên demo; chín tài liệu IT còn lại liên quan bộ QA public-review tháng 9. Hash chín tệp hiện tại khớp kiểm kê; Doc134 ghi rõ là giả lập, không áp dụng trong sản xuất. [Bằng chứng và giới hạn](../reports/rag-14-departments-20261005/math-templates-release-wiring-20261019/department-source-provenance.json) không xác nhận nguồn nghiệp vụ hoặc nghiệm thu nào; chưa kiểm nguồn chia sẻ/folder legacy. HR và Warehouse hiện chỉ thấy nguồn demo/evaluation trong phạm vi này. Cần nguồn và người duyệt nghiệp vụ thật, không sinh thêm bộ câu hỏi giả để lấp số lượng. Bộ 18 tệp đề xuất vẫn nguyên hash, chưa commit.

## 33. Ngày 19/10/2026 — sửa tên tệp trong citation tính toán

Đã sửa nguyên nhân chung của lỗi filename ở mục 30, trong [5 tệp riêng chưa commit](../reports/rag-14-departments-20261005/source-filename-citation-20261019/README.md). So sánh, tổng, BOM và nguồn phụ thuộc lấy tên catalog đúng DocID/phiên bản qua kiểm quyền trong snapshot; không lấy basename đường dẫn, không đổi identity citation. Tên thiếu/rỗng dừng bằng lỗi chung trước khi trả số. Bộ 18 tệp chờ duyệt không đổi hash; chưa có quyền commit mới cho bản sửa này.

93 kiểm tra liên quan đạt sau RED/GREEN, module 93,07% coverage; review tập trung không có finding. Một comparison evaluation trên SQL chính trả đúng tên hai CSV QA Doc1171v1/1172v2 và 1500/1800/300/20%, 0 provider/0 ghi SQL. Không khởi động runtime, không đo formal latency/cost hoặc ghi nghiệm thu; các gate phát hành và mục tiêu đủ 14 phòng vẫn chưa hoàn thành. Không chạy full suite/build/browser/model.

## 34. Ngày 19/10/2026 — phân biệt câu hỏi demo và đầu vào nghiệm thu thật

Kiểm bổ sung catalog và bảng chia sẻ SQL chính không thấy nguồn chia sẻ chéo thêm cho 12 phòng ngoài Accountant/Technical; ba nhóm folder/owner ngoài danh mục đều mang mã QA. Trong SQL đã có 900 câu regression active, đúng 75 câu mỗi phòng, nhưng toàn bộ gắn `DemoBatchID=CreatedBy=demo-wave-v1`. Cả 12 cặp owner/approver cấu hình đều là tài khoản mang tên demo; chưa xác nhận đại diện nghiệp vụ thật. [Bằng chứng chỉ đọc](../reports/rag-14-departments-20261005/math-templates-release-wiring-20261019/department-source-provenance.json) lưu nhóm nguồn/chia sẻ, số câu, batch gate và hash mã bootstrap; không chép mật khẩu hoặc nội dung tài liệu mật.

Không tự coi số dòng, trạng thái pilot hoặc gate demo lưu sẵn là đủ 75 câu hỏi thật/người duyệt/nghiệm thu theo plan. Đã hỏi vị trí tài liệu/câu hỏi nghiệp vụ thật và đầu mối duyệt; chưa đổi dữ liệu hoặc cấp quyền. Phạm vi lượt kiểm này là catalog hiện tại, không khẳng định mọi tệp chưa đăng ký trên máy đều đã được tìm.

Đối chiếu Qdrant: SDK hiện tại vẫn 1.18.0 như lượt 15/10, không có proxy HTTP khai báo trong environment tiến trình kiểm. Dữ liệu HTTP cũ có response cùng cỡ nhận nhanh xen một mẫu chậm; không đủ cơ sở kết luận payload lớn hay client là nguyên nhân. Không mở thêm vòng đo, sửa timeout/retry/transport hoặc gỡ blocker Graph khi chưa có phép kiểm sửa cụ thể. Bộ 18 tệp và bản sửa tên tệp 5 tệp giữ nguyên; chưa commit mới, gọi provider hoặc bật runtime.

## 35. Ngày 19/10/2026 — cảnh báo bộ lọc Excel trong luồng duyệt hiện có

Đã bổ sung [6 tệp riêng chưa commit](../reports/rag-14-departments-20261005/excel-filter-source-review-20261019/README.md) cho khoảng trống Excel ở mục 30. Bộ đọc nhận bộ lọc worksheet/table, giữ đủ dòng và cảnh báo phạm vi cần kiểm; không tự lọc hoặc tính công thức. Dòng ẩn `true`/`1`, công thức và dấu hiệu xung đột cũ được giữ. Dropdown/sort đơn thuần không tạo cảnh báo lọc. Table relationship/path/range và tham chiếu trùng bị kiểm trước khi đọc phần bảng.

Ngữ cảnh nguồn đã kiểm hash hiển thị cảnh báo theo đúng ô cho cả dữ liệu chờ duyệt từ bộ đọc cũ; không thay fact/snapshot/quyết định đã lưu. UI giải thích bằng tiếng Việt và yêu cầu giá trị xác minh, không suy diễn dòng giữ/loại thay reviewer. Review bắt hai P2 về mã giao dịch/tiền tệ bị chặn nhầm và table XML lặp; đã RED/GREEN, sửa và kiểm lại đúng finding. Các kiểm liên quan đạt, coverage từng module trên 80%; không chạy full suite/build/browser/model.

Bộ 18 tệp và 5 tệp trước giữ nguyên hash; chưa có commit, reingest, sửa SQL chính hoặc activation mới. Đây là kiểm bằng workbook tạo trong test/SQLite, chưa phải workbook nghiệp vụ, formal gate hoặc kết quả nghiệm thu ACC-041 hay 14 phòng. Các scope đã duyệt cũ không tự được xác nhận lại chỉ bởi cảnh báo màn hình; trước khi mở rộng nguồn Excel vẫn cần kiểm và duyệt nguồn phù hợp. Nghiệm thu giữ 0/14, bộ nguồn/câu hỏi thật và quyền phát hành tiếp vẫn chờ người dùng.

## 36. Ngày 19/10/2026 — chốt đề xuất gộp 29 tệp, chờ quyết định phát hành tiếp

Đã đóng [gói đúng 29 tệp](../reports/rag-14-departments-20261005/math-release-package-20261019/README.md), gồm 18 tệp tổng/so sánh + 5 tệp tên citation + 6 tệp duyệt nguồn Excel, không phát sinh thêm mã. Manifest/hash và patch chứa cả sáu tệp mới, base vẫn `d7aee7f`, index rỗng. Gói mới thay lời mời duyệt 18 tệp trước để xin một quyết định cho contract cha và một commit local; quyền 20 tệp trước đã dùng, không tự mở rộng sang 29 tệp.

Ba kiểm tích hợp đúng phần giao nhau sau ghép đã đạt: grant cha fixture thực thi tổng/so sánh và thu hồi, picker theo quyền tác vụ, frozen runner/exporter với oracle/binding tổng. Đây là SQLite/fixture/baseline giả lập, không formal thật hoặc chữ ký production. Tái dùng các kiểm/coverage đã đóng từng phần, không chạy lại full suite/review rộng. Audit mới vẫn 62 cảnh báo Python/15 package và 7 npm, name/version/advisory khớp trước; không đổi dependency hoặc gọi đó là security gate sạch.

Chưa stage/commit, ký, gọi provider, sửa SQL chính, khởi động runtime, push hoặc bật tính năng. Sau quyết định và commit cuối mới đóng đầu vào formal và xin quyền tương ứng. Các gate Graph/Query, blocker Qdrant, nguồn/câu hỏi/người duyệt thật và nghiệm thu đủ 14 phòng còn nguyên; gói 29 tệp không thay điều kiện hoàn thành toàn plan.

## 37. Ngày 19/10/2026 — đã duyệt contract và commit local 29 tệp

Người dùng duyệt trực tiếp contract và commit local [gói 29 tệp](../reports/rag-14-departments-20261005/math-release-package-20261019/README.md). Đã chốt tại **`64b8b70568dcf95fc4166fb3306907ae4fe40b77`**, cha `d7aee7f`, đúng 29 tệp và đúng hash mã đã gửi duyệt (index chuẩn hóa LF). Hook bảo mật chuẩn chạy, không bypass; kiểm hash 11 tệp WIP ngoài gói giữ nguyên khi commit. Product paths sạch, index rỗng. Quyền commit 29 tệp đã dùng hết; plan/handoff/gitignore/coverage không nằm trong commit.

Tái dùng kiểm/coverage và ba kiểm tích hợp đã đóng trên đúng mã; không chạy lại test hoặc build. 280 phiên bản package Python audit vẫn khớp, manifest npm không đổi; cảnh báo dependency cũ vẫn mở, không coi là security gate sạch. Không paid API, ký authority thật, sửa SQL chính, migration, restart runtime, push hoặc activation. Contract cha được duyệt không thay các ngưỡng đã chốt hoặc tự làm gate formal đạt.

Bước tiếp là đóng đầu vào baseline/candidate và quyền phát hành trên commit cuối bằng runner hiện hữu, trước khi đề xuất provider/ký/triển khai cụ thể. Các cửa sổ cũ không tái dùng. Goal đủ 14 phòng tiếp tục; nguồn/câu hỏi/người duyệt thật, blocker Qdrant, các gate và nghiệm thu nghiệp vụ 0/14 vẫn chưa hoàn thành.

Sau yêu cầu rà soát cách làm của người dùng, dừng mở rộng case/framework/review và chuyển sang đầu ra phát hành: [đã build gói UI production và kiểm nguồn thật chỉ đọc](../reports/rag-14-departments-20261005/math-release-formal-preparation-20261019/README.md) trên 64b8b705. Nguồn/danh tính giữ nguyên, không chạy lại candidate hoặc bộ test cũ. Kiểm đường cập nhật main từ f58d2e1 qua đủ năm commit không thấy WIP trùng đường dẫn; chưa cập nhật main hoặc chạy runtime. Bước thực thi tiếp là runner đã có và một đề xuất gộp quyền mới còn thiếu; không bắt Math trên hai nguồn QA chờ Graph hay đủ 1.050 câu. Build/đóng đầu vào không phải đã triển khai hoặc nghiệm thu.

[Formal trên commit64b8b705 đã hoàn tất](../reports/rag-14-departments-20261005/math-release-formal-preparation-20261019/formal-completed.json) sau hai quyết định paidcall trực tiếp: lượt đầu dừng vì cache BM25 thiếu,0call/policyđãđóng; sửa đúng cache và kiểm bốn ranh giới generation bị chặn mạng, rồi cửa sổ mới dùng đúng12call baseline thành công/0retry. Tổng và so sánh đều đạt ba cặp độc lập; candidate0call,6bước tổng/24bước so sánh, giá trị và citation đúng oracle/phiên bản. Hai policy inactive, runtime đo đóng. Tái dùng runner, câu hỏi, nguồn và kiểm product; không đổi gate, oracle hoặc thêm bộ case. [Hồ sơ review và đề xuất ký/kiểm/bật có rollback](../reports/rag-14-departments-20261005/math-release-formal-preparation-20261019/primary-release-proposal.json) đã đóng trên đúnghai nguồn QA, chưa ký hoặc đổi main/runtime, không tự ghi acceptance. Gate tổ hợp thực tế/startup/rollback và quyền phát hành là bước còn thiếu; theo dõi7ngày/100request thật và nghiệm thu0/14 vẫn riêng.
