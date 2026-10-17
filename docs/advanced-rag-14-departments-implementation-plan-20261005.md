# Kế hoạch hoàn thiện Advanced RAG cho 14 phòng ban

Ngày tạo: 05/10/2026. Ngày cập nhật: 17/10/2026.

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

- Giữ nguyên Math v1. Bổ sung Math v2 qua hai mẫu nghiệp vụ cố định:
  1. So sánh hai phạm vi: tổng A, tổng B, chênh lệch và tỷ lệ thay đổi.
  2. Tổng hợp BOM từ manifest occurrence đầy đủ, có cơ sở số lượng rõ ràng.
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
