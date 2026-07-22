# Kế hoạch refactor codebase theo deep module và dependency một chiều

Trạng thái: **In progress — Phase 0, Phase 1 và Phase 2 đã hoàn tất; Phase 3 chưa bắt đầu**

Ngày lập kế hoạch: **2026-07-20**

Phạm vi đã chốt: **backend cốt lõi**

Chính sách tương thích: **giữ nguyên toàn bộ contract hiện tại**

Nguồn đối chiếu chính: **tài liệu này**

## 1. Mục tiêu và cách sử dụng tài liệu

### 1.1. Đích đến

Refactor backend thành các module sâu: mỗi module có một interface nhỏ, sở hữu trọn một hành vi có ý nghĩa, che giấu orchestration và dependency bên trong. Sau khi hoàn tất:

- FastAPI router và worker chỉ còn là adapter nhận/gửi dữ liệu.
- Logic chat, document workflow, ingestion và RAG có owner rõ ràng.
- Dependency đi một chiều từ delivery vào application/domain rồi ra adapter.
- Không module application/domain nào tự tạo SQL engine, Qdrant client, LLM, Vision model hoặc đọc environment tại nơi sử dụng.
- `RagExecutor.run()` tiếp tục là interface công khai duy nhất để chạy một request RAG.
- HTTP, SSE, RBAC, database schema, feature flag, trace/evaluation contract và hành vi rollout không thay đổi.

### 1.2. Tài liệu này là refactor ledger

Mỗi phase phải cập nhật trực tiếp các trường sau trước khi được đánh dấu hoàn tất:

| Trường evidence | Nội dung bắt buộc |
|---|---|
| Baseline | Commit, branch, trạng thái working tree và test result trước phase |
| Contract được bảo vệ | Interface và observable behavior không được đổi |
| RED | Tên test mới và lý do nó fail trước implementation |
| GREEN | Commit hoặc diff làm test pass qua interface mới |
| Validation | Lệnh đã chạy, exit code, số test pass/fail/skip và coverage |
| Architecture delta | Violation nào được xóa khỏi allowlist/ratchet |
| Known issues | Lỗi còn mở, warning và điều kiện môi trường |
| Rollback | Commit hoặc phase boundary có thể revert độc lập |

Không đánh dấu `Completed` chỉ vì code đã được di chuyển. Trạng thái hợp lệ của một phase là:

```text
Not started -> Characterized -> RED -> GREEN -> Reviewed -> Validated -> Completed
```

Nếu unit/contract gate pass nhưng service, SQL/Qdrant fixture hoặc provider cần cho integration chưa sẵn sàng, trạng thái duy nhất được phép là `Validated offline / Blocked integration`. Trạng thái này không tương đương `Completed` và không cho phép bắt đầu phase phụ thuộc tiếp theo.

### 1.3. Ngoài phạm vi

- Không redesign Vue; chỉ sửa frontend nếu cần giữ nguyên contract browser sau refactor backend.
- Không đổi database schema hoặc chạy data migration.
- Không thay thuật toán retrieval, ranking, prompt, evidence policy hoặc quality threshold.
- Không bật thêm CRAG, claim repair, grounded math, decomposition, graph hoặc community summary.
- Không dùng refactor/test pass làm bằng chứng chấp thuận live rollout.
- Không xóa compatibility shim được coi là public nếu chưa có inventory và deprecation decision riêng.

## 2. Baseline hiện tại và invariant phải giữ

### 2.1. Hotspot đã xác nhận

| Khu vực | Baseline hiện tại | Vấn đề chính |
|---|---:|---|
| `api/app_server.py` | 2.763 dòng, 125 route, 169 top-level function | HTTP, RAG proxy, citation, persistence, audit, upload, publication và SQL trộn trong một module |
| `rag/pipeline.py` | 2.017 dòng | `execute_pipeline()` kéo dài từ khoảng dòng 202 đến 1.802 và sở hữu gần toàn bộ orchestration |
| `rag/pipeline_steps.py` | 1.366 dòng | Nhiều phase helper cùng phụ thuộc global RAG/LLM/DB state |
| `ingestion/pdf/pipeline.py` | 979 dòng | Extraction, Vision, metadata, SQL, Qdrant, quality và rollback cùng implementation |
| `workers/ingestion_worker.py` | 189 dòng, một `run_worker()` | Polling, classification, raw SQL, state transition và error policy cùng một vòng lặp |
| `services/` | 18 file, 0 function/class, 131 export entry | Migration facade nông; `app_server` import khoảng 119 tên từ namespace phẳng |
| Architecture guard | 8/8 pass | Đang quét `src/mech_chatbot/ui` cũ nên không bắt violation trong FastAPI hiện tại |

### 2.2. Baseline validation ngày 2026-07-20

- `test_app_chat_orchestration.py`, `test_rag_execution_contract.py` và `test_markdown_ingestion.py`: **47 test pass, exit code 0**.
- Architecture guard độc lập: **8/8 pass, exit code 0**.
- Test process hai lần in `Windows fatal exception: access violation` trong đường import `pyarrow -> pandas -> sklearn -> sentence_transformers`, sau đó pytest vẫn trả exit code 0. Đây là **known baseline infrastructure issue**; một run có fatal diagnostic không được coi là validation sạch cho phase refactor.
- Working tree hiện có nhiều thay đổi chưa commit, gồm các file trùng phạm vi tương lai như `rag_server.py`, `settings.py`, `rag/pipeline.py` và `services/graph_service.py`.

### 2.3. Precondition trước khi implement Phase 0

1. Chốt một commit nền đã review cho công việc retrieval-intelligence đang dở. Không tự `stash`, reset hoặc ghi đè thay đổi hiện tại.
2. Tạo branch refactor riêng từ commit nền bằng prefix `codex/`.
3. Ghi commit SHA, `git status --short` và diff liên quan vào ledger này.
4. Tái hiện native import diagnostic bằng một run tuần tự. Nếu `import pyarrow` độc lập crash, dựng lại môi trường từ lock trong một virtual environment mới trước khi sửa source. Nếu chỉ pytest path gây diagnostic, cô lập file/import path và ghi thành baseline blocker; không chấp nhận exit code 0 kèm fatal diagnostic làm gate.
5. Chụp OpenAPI của `app_server` và `rag_server`, cùng một transcript SSE thành công, một transcript busy/error và response mẫu của upload/review. Các artifact phải loại secrets và nội dung nhạy cảm.

### 2.4. Invariant toàn chương trình

- Browser endpoints, request/response field, status code và SSE event name/order giữ nguyên.
- `thinking`, `delta`/`token`, `citation`, `warning`, `error`, `done` giữ semantic hiện tại.
- User profile và RBAC luôn được resolve server-side; không tin `allowed_departments` hoặc clearance do client gửi.
- Evidence manifest và answer-attributed citation tiếp tục được lưu tách biệt.
- Confidential source access tiếp tục được audit.
- SQL vẫn là workflow source of truth; Qdrant chỉ phục vụ point có metadata hợp lệ và `servable=true`.
- Budget, deadline, cancellation, execution mode và fail-closed behavior của typed RAG events không đổi.
- Evaluation/pilot replay không tạo chat history hoặc user-visible side effect.
- Tất cả feature flag giữ default hiện tại; refactor không mở rollout gate.

## 3. Kiến trúc mục tiêu

```text
Vue / internal clients
        |
        v
FastAPI routers / worker loop                 delivery adapters
        |
        v
Application modules theo use case            deep modules
        |
        +--> pure policy/domain modules
        |
        v
Ports tại system seam
        |
        +--> SQL adapters
        +--> Qdrant/vector adapters
        +--> internal RAG HTTP adapter
        +--> LLM/Vision adapters
        +--> filesystem adapter
```

RAG giữ topology riêng:

```text
RAG API / worker / evaluation
        |
        v
RagExecutor.run(request, invocation, cancellation)
        |
        v
private preparation -> routing -> retrieval -> evidence -> generation
        |
        v
SQL / Qdrant / LLM adapters
```

Hai sơ đồ trên mô tả runtime call flow. Source-code dependency được đảo tại port: `composition` được phép import delivery/application/adapter để wire; delivery import application; adapter implement và import port/type do application sở hữu; application/domain không import adapter hay framework bên ngoài.

Quy tắc seam:

- Không tạo `IService` hoặc repository interface cho mọi CRUD.
- Chỉ tạo port khi có production adapter và fake/in-memory adapter dùng trong test.
- Pure computation được gọi trực tiếp, không bọc thêm pass-through.
- Test chính đi qua interface của deep module; không mock các helper nội bộ.
- Compatibility shim là lớp chuyển tiếp tạm thời, không phải nơi thêm logic mới.

Layout đích được khóa để implementer không phải tự chọn lại cấu trúc:

```text
src/mech_chatbot/
  application/
    chat_turn.py
    document_upload.py
    document_review.py
    protected_files.py
    ingestion.py
  adapters/
    rag_http.py
    filesystem.py
  composition/
    app_runtime.py
    rag_runtime.py
    worker_runtime.py
  rag/
    execution.py                 # public execution seam, giữ nguyên
    phases/                      # private preparation/routing/retrieval/evidence/generation
  db/repositories/              # SQL adapters hiện có, tiếp tục tách theo domain
  llm/                          # provider adapters hiện có
```

Protocol/port được đặt cạnh application module sở hữu nó, không gom vào một file `ports.py` toàn cục. SQL adapter tiếp tục nằm trong `db/repositories`; không di chuyển file chỉ để khớp tên tầng. Composition root được tạo dần ngay khi deep module xuất hiện: `build_app_runtime(settings)` ở Phase 1, `build_worker_runtime(settings)` ở Phase 3 và `build_rag_runtime(settings)` ở đầu Phase 4. Phase 5 chỉ hoàn tất việc gom dependency còn sót, xóa import-time singleton và phá các cạnh dependency ngược; không chờ đến Phase 5 mới bắt đầu dependency injection.

Các test seam được chốt cho roadmap:

1. HTTP/OpenAPI/SSE cho browser-facing contract.
2. `ChatTurnRunner.stream()` cho toàn bộ chat-turn behavior.
3. `DocumentUpload.enqueue()`, `ReviewDocuments.execute()`, `PublicationCoordinator.publish_job()` và `ProtectedFileResolver.resolve()` cho document workflows.
4. `IngestionRunner.run()` cho một ingestion lifecycle hoàn chỉnh.
5. `RagExecutor.run()` cho execution lifecycle và mọi RAG phase extraction.
6. SQL/Qdrant/HTTP/LLM/filesystem adapter contract tại system seam; không tạo test seam cho helper private.

## 4. Roadmap thực hiện

## Phase 0 — Baseline tin cậy và architecture ratchet

### Mục cần refactor

- Architecture test đang nằm ngoài default `pytest` collection.
- Guard UI/API đang trỏ vào package UI Python không còn tồn tại.
- Chưa có machine-checkable rule ngăn dependency ngược hoặc debt tăng thêm.
- Chưa có coverage gate chính thức trong CI.

### Gợi ý refactor

Đưa architecture guard vào `tests/architecture/` để default suite thu thập. Tạo dependency ratchet đọc AST source hiện tại và dùng allowlist cho debt có sẵn; mỗi phase sau phải xóa entry, không được thêm entry.

Các rule bắt buộc:

1. `api/` không được thêm import mới từ `db.engine`, `db.repositories` hoặc `sqlalchemy`; các violation hiện tại được allowlist theo file và loại violation.
2. `application/` không import `adapters/`, FastAPI, SQLAlchemy, Qdrant SDK hoặc provider SDK.
3. `db/` không được thêm import sang `rag/`, `ingestion/`, `api/` hoặc `evaluation/`.
4. `rag/` không import `evaluation/`; `evaluation/` chỉ được gọi public RAG contract.
5. Không tăng số `import *`, `os.getenv` ngoài bootstrap/config hoặc import-time singleton.
6. `services/__init__.py` không được thêm export mới.

### Cách làm chi tiết

1. Characterize: thêm test chứng minh guard hiện tại vẫn pass dù `app_server.py` dùng `engine`/raw SQL.
2. RED: thêm architecture test quét đúng `api/`; test phải fail và liệt kê đúng violation hiện tại.
3. GREEN: thêm allowlist baseline có chú thích phase sẽ xóa từng entry; test pass nhưng fail với một synthetic/new violation.
4. Di chuyển hoặc thay thế layered guard cũ; default `pytest` phải thu thập architecture tests.
5. Thêm coverage command cho Python và Vue CI. Tạo `scripts/quality/check_coverage.py` đọc JSON của coverage.py và fail độc lập khi line hoặc branch coverage dưới ngưỡng, tránh dựa vào một tỷ lệ tổng hợp. Intermediate gate: từng package refactor-owned (`application`, `adapters`, `composition`, `rag/phases`) đạt ít nhất 80% line và branch coverage. Final gate dùng toàn bộ `mech_chatbot`; nếu baseline toàn backend thấp hơn 80%, Phase 0 phải bổ sung characterization test trước khi Phase 1 bắt đầu, không hạ ngưỡng.
6. Ghi OpenAPI/SSE baseline hash và known native import diagnostic vào ledger. Artifact chuẩn nằm dưới `reports/refactor/phase-0/baseline/`: `openapi-app.json`, `openapi-rag.json`, `sse-success.jsonl`, `sse-busy.jsonl`, `upload-review-samples.json` và `pytest-baseline.txt`.
7. Trước khi hash/diff, canonicalize timestamp, trace/request ID, elapsed time, absolute path, token/secret và nội dung tài liệu nhạy cảm. Mỗi thư mục artifact có `manifest.json` ghi commit SHA, fixture/data snapshot, settings fingerprint đã lọc secret, Python dependency lock hash và lệnh tạo artifact.

### Test và acceptance gate

- Architecture test phát hiện được import DB/SQL mới trong một fixture giả.
- Default `pytest --collect-only` chứa architecture tests.
- Không test nào phụ thuộc DB/Qdrant/LLM thật.
- Fast unit/security suite pass sạch, không có fatal native diagnostic.
- `git diff --check` pass.

### Kết quả mong đợi và điểm debug

- Debt kiến trúc trở thành danh sách hữu hạn, có thể đếm và giảm sau mỗi phase.
- Khi regression dependency xảy ra, output chỉ rõ source package, target package và rule bị vi phạm.
- Rollback: revert riêng commit architecture-test/CI; không đụng application behavior.

## Phase 1 — Deepen browser chat thành `ChatTurnRunner`

### Mục cần refactor

`app_server.chat_message()` hiện sở hữu HTTP/SSE transport, pilot routing/replay, citation attribution, persistence, audit và error translation. Test phải monkeypatch global `requests`, persistence và audit functions.

### Gợi ý refactor

Tạo một deep application module `ChatTurnRunner` sở hữu trọn chat turn từ RAG stream tới citation, persistence, audit và pilot replay. Router chỉ chuyển HTTP sang command, typed event sang SSE; external RAG, SQL, audit và pilot là port có adapter cụ thể.

### Interface mục tiêu

```python
@dataclass(frozen=True, slots=True)
class ChatActor:
    user_id: int
    username: str
    roles: frozenset[str]
    department: str | None
    allowed_departments: frozenset[str]
    allowed_sites: frozenset[str]
    max_security_level: str
    response_language: str

@dataclass(frozen=True, slots=True)
class ChatTurnCommand:
    request_id: str
    session_id: str
    question: str
    image_path: Path | None
    history: tuple[Mapping[str, Any], ...]
    current_part_ids: tuple[str, ...]
    conversation_context: Mapping[str, Any] | None

@dataclass(frozen=True, slots=True)
class ChatThinking:
    message: str

@dataclass(frozen=True, slots=True)
class ChatDelta:
    text: str

@dataclass(frozen=True, slots=True)
class ChatCitation:
    citation: Mapping[str, Any]

@dataclass(frozen=True, slots=True)
class ChatWarning:
    code: str
    message: str
    detail: str | Mapping[str, Any] | None = None

@dataclass(frozen=True, slots=True)
class ChatError:
    code: str
    message: str
    http_status: int | None = None
    retryable: bool = False
    elapsed_ms: int | None = None
    detail: str | Mapping[str, Any] | None = None

@dataclass(frozen=True, slots=True)
class ChatDone:
    chat_id: int | None
    ref_text: str
    citations: tuple[Mapping[str, Any], ...]
    new_part_ids: tuple[str, ...]
    conversation_context: Mapping[str, Any] | None
    elapsed_ms: int | None

ChatTurnEvent = ChatThinking | ChatDelta | ChatCitation | ChatWarning | ChatError | ChatDone

class ChatTurnRunner:
    def stream(self, command: ChatTurnCommand, actor: ChatActor) -> Iterator[ChatTurnEvent]: ...
```

Ports tại seam hệ thống:

- `RagStreamPort.stream(request, route)`: production dùng HTTP/SSE adapter; test dùng scripted in-memory adapter.
- `ChatStore`: production dùng SQL adapter; test dùng in-memory adapter.
- `AuditSink`: production dùng SQL audit adapter; test dùng recording adapter.
- `PilotExperimentPort.assign(actor, command)` và `schedule_replay(assignment, replay_input)`: production adapter bọc hành vi hiện tại trong `evaluation.crag_pilot`; test dùng deterministic fake. Application không import `evaluation` và pilot replay không được tạo lịch sử chat hoặc side effect nhìn thấy bởi user.

### Cách làm chi tiết

1. Giữ endpoint và Pydantic schema hiện tại làm outer contract.
2. RED từng tracer bullet qua `ChatTurnRunner.stream()`:
   - success event order và answer aggregation;
   - citation attribution/ref text;
   - chat/evidence/source persistence;
   - confidential audit;
   - RAG 503/busy không persistence;
   - stream thiếu `done` fail closed;
   - persistence failure phát `warning` nhưng vẫn hoàn tất response;
   - pilot assignment, bounded replay và drop behavior.
3. GREEN bằng cách di chuyển orchestration nguyên trạng vào runner, chưa tối ưu thuật toán. Typed event phải chứa đủ dữ liệu để serializer tái tạo payload SSE hiện tại; không dùng `kind + Mapping` làm contract nội bộ vì sẽ đẩy lỗi field về runtime.
4. Tạo HTTP RAG adapter chịu trách nhiệm timeout, headers, `requests.post`, parse SSE và mapping lỗi transport. Adapter không lưu chat hoặc audit.
5. Tạo `build_app_runtime(existing_settings)` trong `composition/app_runtime.py` ngay trong phase này. Factory dựng `ChatTurnRunner` và adapter từ `Settings` hiện có; router/lifespan lấy frozen runtime bundle, không dùng temporary global hoặc service locator.
6. Router chỉ: resolve actor gồm `department`, verify image token, tạo command với request ID tương đương `<session_id>|<uuid>`, gọi runner và serialize typed `ChatTurnEvent` sang đúng SSE event/payload hiện tại.
7. Chuyển test cũ từ monkeypatch helper nội bộ sang scripted external adapters; giữ một nhóm endpoint contract test mỏng.
8. Xóa architecture allowlist cho `requests`/persistence orchestration trong chat router nếu rule đã đạt.

### Test và acceptance gate

- Toàn bộ chat characterization tests cũ pass.
- Test mới gọi `ChatTurnRunner.stream`, không gọi private method và không assert call order của internal helper.
- OpenAPI và sanitized SSE transcript không đổi.
- RBAC/service-token, CSRF, image ownership và citation access tests pass.
- Không HTTP provider/SQL thật trong unit test.
- Changed module coverage >=80% cho cả line và branch.

### Kết quả mong đợi và điểm debug

- Một chat request có một owner duy nhất từ RAG call đến persistence/audit.
- Có thể tái hiện lỗi bằng một scripted RAG event sequence mà không chạy RAG server.
- Router không chứa RAG HTTP loop, citation business rule, persistence hoặc audit branching.
- Rollback: `git revert` các commit Phase 1 theo thứ tự ngược. Không giữ hai implementation chat old/new, không thêm runtime toggle và không đổi wire contract hoặc data.

## Phase 2 — Deepen document upload, review, publication và protected-file access

### Mục cần refactor

- Upload endpoint tự validate, ghi filesystem và enqueue DB.
- Bulk review/publish điều phối nhiều repository operation trong router.
- Một số endpoint chạy raw SQL trực tiếp.
- `api/file_access.py` trộn SQL authorization query và filesystem resolution.
- `services/` vẫn là namespace phẳng chứa pass-through operation.

### Gợi ý refactor

Tách theo use case, không theo CRUD: upload, review/publication và protected-file access là ba application owner riêng. Filesystem, SQL publication/job store và authorization query là adapter; router chỉ validate transport shape và serialize result về contract cũ.

### Interface mục tiêu

```python
@dataclass(frozen=True, slots=True)
class UploadDocumentCommand:
    file_name: str
    content: bytes
    owner_department: str
    shared_departments: tuple[str, ...]
    domain: str | None
    security_level: str | None
    process_stage: str | None
    site: str | None
    upload_metadata: Mapping[str, Any]

@dataclass(frozen=True, slots=True)
class UploadReceipt:
    job_id: int
    file_name: str
    owner_department: str

@dataclass(frozen=True, slots=True)
class UploadFailure:
    code: str
    file_name: str
    message: str
    detail: Mapping[str, Any] | None = None

@dataclass(frozen=True, slots=True)
class UploadBatchResult:
    jobs: tuple[UploadReceipt, ...]
    errors: tuple[UploadFailure, ...]
    created: int
    failed: int

    @property
    def ok(self) -> bool:
        return self.failed == 0

@dataclass(frozen=True, slots=True)
class ReviewItem:
    job_id: int | None
    doc_id: int | None

@dataclass(frozen=True, slots=True)
class ReviewDocumentsCommand:
    action: Literal["publish", "reject", "delete"]
    publish_mode: Literal["standalone", "new_version", "new_variant"]
    reason: str | None
    items: tuple[ReviewItem, ...]

@dataclass(frozen=True, slots=True)
class ReviewItemOutcome:
    status: Literal["updated", "pending", "failed"]
    job_id: int | None
    doc_id: int | None
    code: str | None
    message: str | None
    detail: Mapping[str, Any] | None = None

@dataclass(frozen=True, slots=True)
class BatchReviewResult:
    outcomes: tuple[ReviewItemOutcome, ...]
    updated: int
    pending: int
    failed: int

DocumentUpload.enqueue(command, actor) -> UploadReceipt
DocumentUpload.enqueue_batch(commands, actor) -> UploadBatchResult
ReviewDocuments.execute(command, actor) -> BatchReviewResult
PublicationCoordinator.publish_job(command, actor) -> PublicationOutcome
ProtectedFileResolver.resolve(reference, actor) -> AuthorizedFile
```

`PublicationCommand` chứa `job_id`, `doc_id`, `publish_mode` và actor context; route theo job được phép đưa `doc_id=None`, khi đó `JobStore` resolve latest document đúng như raw SQL hiện tại. `ProtectedFileReference` chứa loại reference, document/page hoặc image identifier và requested path; `AuthorizedFile` chỉ trả resolved absolute path sau khi RBAC và allowed-root đều pass. Các result là frozen dataclass có status/code/message/data rõ ràng; FastAPI adapter chịu trách nhiệm map sang đúng HTTP status/response snapshot hiện tại.

Error code tối thiểu được khóa: `invalid_extension`, `empty_file`, `file_too_large`, `invalid_batch`, `missing_job_id`, `missing_doc_id`, `unauthorized`, `not_found`, `storage_failed`, `enqueue_failed`, `cleanup_failed`, `publish_contract_failed`, `publication_pending`, `job_reject_failed` và `delete_failed`. Router không parse exception text để quyết định status.

### Cách làm chi tiết

1. Làm từng vertical slice theo thứ tự: protected files -> single upload -> batch upload -> single publication -> bulk review.
2. Mỗi slice bắt đầu bằng endpoint characterization test cho success, unauthorized, invalid input và partial failure. Snapshot phải chốt chính xác body/status hiện tại trước khi chuyển logic.
3. Single upload giữ invariant hiện tại: extension allowlist, file không rỗng, tối đa 100 MB, department authorization và sanitized department folder. Nếu file đã ghi nhưng enqueue thất bại, filesystem adapter phải xóa file; nếu cleanup cũng thất bại, ghi audit/cleanup error và trả `cleanup_failed` detail nhưng không báo đã tạo job.
4. Batch upload giới hạn 50 file và xử lý độc lập từng file. Job đã tạo thành công không rollback khi file sau lỗi. Response serializer giữ `{ok, jobs, errors, created, failed}` với `ok = failed == 0`.
5. API hiện không có idempotency key: retry tạo stored file/job mới. Phase này phải giữ nguyên hành vi đó và không tự bổ sung idempotency. Chỉ thêm idempotency trong feature decision riêng có schema/API design và migration plan.
6. Bulk review xử lý từng item và giữ partial-success contract. Input thiếu `items`/action không hợp lệ map HTTP 400; actor không đủ role map 403; business failure theo item vẫn trả HTTP 200 với `{ok, updated, pending, failed, failures}`. `publish` yêu cầu `job_id` và `doc_id`; `reject` yêu cầu `job_id`; `delete` chấp nhận `doc_id`, `job_id` hoặc cả hai. Transport normalize `publish_mode` thiếu/không biết thành `standalone`, không trả 400. Characterization phải khóa cả hai behavior lạ hiện tại: delete thiếu cả hai ID là no-op được đếm `updated`, và reject fallback không kiểm tra giá trị trả về cuối; không âm thầm “sửa đúng” chúng trong structural refactor. Mọi thay đổi hai behavior này cần bug/security decision riêng.
7. Single publication theo job dùng `JobStore` resolve latest `doc_id`; không có document map 404. `standalone`, `new_version`, `new_variant`, outbox state và mapping `published`/`pending` giữ nguyên. Coordinator dùng publication contract hiện tại, không trực tiếp flip `IsCurrent` hoặc Qdrant visibility.
8. Di chuyển actor validation, business invariant và multi-step coordination vào application module. Di chuyển raw SQL sang repository/SQL adapter có parameterized query; không tạo application wrapper chỉ để forward một CRUD call.
9. Tách filesystem adapter chịu path normalization, allowed-root check và atomic write. Application module chỉ nhận logical file reference; adapter phân biệt `not_found`, `unauthorized` và `storage_failed`.
10. Sau khi logic có owner mới, chia transport thành `api/routers/chat.py`, `api/routers/documents.py` và `api/routers/operations.py`; `api/app_server.py` chỉ còn app factory, SPA wiring và compatibility export trong thời gian migrate. Không split router trước khi behavior đã có owner mới.
11. Mở rộng `build_app_runtime(existing_settings)` đã tạo ở Phase 1 để wire document/file modules và adapters; không thêm module global tạm trong lúc split router.
12. Các test và script đang import symbol trực tiếp từ `app_server.py`, gồm chat/document/graph tests và `scripts/graph_eval/exercise_review.py`, phải chuyển sang application interface hoặc HTTP contract. Trong thời gian đó, giữ wrapper cùng tên ở `app_server.py` gọi owner mới; wrapper không chứa duplicate logic và chỉ xóa theo chính sách Phase 6.
13. Với read-only/simple CRUD, dùng explicit feature query adapter; không export lại qua một global `services` namespace. Xóa từng service pass-through export chỉ khi mọi repo caller trong slice đã dùng import/interface mới.

### Test và acceptance gate

- Auth/CSRF/RBAC matrix cho upload, review, publish và file access giữ nguyên.
- Path traversal, dot-file, missing file, unauthorized document/page và chat image ownership fail closed.
- Publication integration tests giữ SQL/Qdrant outbox/servable behavior.
- Bulk operation trả đúng updated/pending/failed/failures như baseline.
- Upload contract tests xác nhận cleanup khi enqueue lỗi, batch partial success, giới hạn 50 file và retry không-idempotent hiện tại.
- Review contract tests xác nhận từng error code và HTTP mapping nêu trên; serializer snapshot giữ nguyên tên field hiện tại.
- API OpenAPI snapshot và Vue API tests pass; không cần redesign frontend.
- `api/app_server.py` và feature routers không import `db.engine`, `db.repositories`, `sqlalchemy` hoặc gọi raw SQL.

### Kết quả mong đợi và điểm debug

- Mỗi upload/review/publication operation có command/result và error code ổn định.
- Partial failure được truy ngược từ `BatchReviewResult`, không phải đọc log exception chung.
- File authorization và filesystem failure được phân biệt.
- Rollback bằng `git revert` từng vertical-slice commit; không giữ implementation cũ song song. Publication outbox là data recovery path, không cần schema rollback.

## Phase 3 — Tạo một owner cho ingestion lifecycle

### Mục cần refactor

- Worker loop vừa poll, classify, chạy SQL, ingest, quyết định quality state và retry.
- `file_ingestor.learn_new_file()` chỉ là dispatcher mỏng.
- PDF/file pipeline sở hữu extraction, Vision, SQL, Qdrant, metadata, quality và rollback.
- Progress callback dùng string magic như `__STATUS__:embedding`.

### Gợi ý refactor

Đặt toàn bộ lifecycle của một job sau khi claim vào `IngestionRunner`, gồm progress, persistence, quality outcome và rollback. Worker trở thành process adapter cho polling/backoff/reconciliation; SQL/Qdrant/Vision/filesystem được đưa vào runner qua port.

### Interface mục tiêu

```python
@dataclass(frozen=True, slots=True)
class IngestionJob:
    job_id: int
    file_path: Path
    file_name: str
    owner_department: str
    shared_departments: tuple[str, ...]
    domain: str | None
    security_level: str | None
    process_stage: str | None
    site: str | None

@dataclass(frozen=True, slots=True)
class IngestionResult:
    outcome: Literal["pending_review", "blocked", "waiting_quota", "failed"]
    report: Mapping[str, Any]
    reason_code: str | None
    message: str

class IngestionRunner:
    def run(self, job: IngestionJob) -> IngestionResult: ...
```

`IngestionJobStore` sở hữu `claim_next`, normalization dữ liệu DB sang `IngestionJob`, mọi status/progress/report/final transition và audit job. Adapter phải normalize `PhongBan` dù DB trả comma-separated string hay collection thành `shared_departments: tuple[str, ...]`. Internal progress dùng typed event: `classifying`, `extracting`, `embedding`, `quality_check`, `completed`.

### Cách làm chi tiết

1. RED contract tests cho state transitions hiện tại: success/pending review, blocked quality, report-persistence failure, quota error, unexpected error và rollback.
2. GREEN: bọc implementation hiện tại bằng `IngestionRunner` trước. Runner sở hữu toàn bộ transition từ job đã claim qua progress, report persistence, final outcome và rollback thông qua `IngestionJobStore`; không trả việc persist result về worker.
3. Worker chỉ gọi `claim_next`, `runner.run`, ghi structured log/metric cho result và điều khiển backoff/reconciler. Worker không classify, không raw SQL và không tự ghi trạng thái kết quả.
4. Di chuyển classification/status/progress/report SQL update vào `IngestionJobStore` adapter.
5. Tách internal phase theo dữ liệu chuyển giao rõ ràng:
   - load/classify source;
   - extract pages/content;
   - enrich Vision/metadata/BOM;
   - persist SQL snapshot;
   - index Qdrant vectors;
   - calculate quality/finalize report.
6. Giữ các phase private; test external behavior qua runner, chỉ test riêng pure parser/quality policy đã có.
7. Thay string progress callback bằng typed progress event; `IngestionJobStore` adapter map event về status/message hiện tại.
8. Reconciliation scheduling tách khỏi per-job runner nhưng vẫn do worker process sở hữu.
9. Tạo `build_worker_runtime(existing_settings)` trong `composition/worker_runtime.py` ngay ở phase này. Runtime bundle dựng runner, JobStore, clock/backoff và adapter; không tạo temporary global.
10. Giữ `process_and_ingest_pdf/file` làm compatibility wrapper trong phase này; wrapper chỉ forward vào owner mới và không chứa duplicate pipeline.

### Test và acceptance gate

- Markdown/PDF unit tests và ingestion quality tests pass.
- Worker contract test dùng fake JobStore/Runner/Clock; không `sleep` thật, SQL thật hoặc provider thật.
- SQL/Qdrant consistency integration test bắt buộc chạy trên fixture/snapshot đã pin. Nếu environment hoặc fixture chưa sẵn sàng, phase chỉ được ghi `Validated offline / Blocked integration`, không được đánh dấu `Completed`.
- Failure trước vector indexing không để document servable.
- Failure sau SQL snapshot kích hoạt rollback/restore hiện tại.
- Quota error đi `waiting_quota`; lỗi khác không bị phân loại nhầm.
- Changed module coverage >=80% cho cả line và branch.

### Kết quả mong đợi và điểm debug

- Một JobID luôn có typed progress trail, final outcome và reason code.
- Worker loop không chứa raw SQL, classification hoặc quality business logic.
- Có thể replay một job bằng fake adapters để xác định phase lỗi.
- Rollback bằng `git revert` các commit Phase 3; không giữ old/new runner song song. Compatibility entrypoint và DB schema không đổi.

## Phase 4 — Chia RAG implementation phía sau `RagExecutor`

### Mục cần refactor

- External execution seam đã tốt nhưng `execute_pipeline()` vẫn là orchestration monolith.
- Routing, retrieval, decomposition, graph, correction, evidence, generation và cache branching nằm chung.
- Budget control-flow error còn phải được bảo vệ thủ công tại một số catch-all.
- Wildcard imports làm interface nội bộ khó đọc.

### Gợi ý refactor

Giữ `RagExecutor.run()` làm public facade sâu và chỉ tách implementation phía sau nó thành các phase private có typed handoff. Mỗi extraction là một behavior-preserving commit; không biến từng phase thành public service hoặc cho caller bypass executor.

### Interface giữ nguyên

```python
RagExecutor.run(
    request: RagRequest,
    invocation: RagInvocation,
    cancellation: CancellationSignal,
) -> Iterator[RagEvent]
```

Không thêm public interface cho từng phase. Internal contracts dự kiến:

```text
prepare(state) -> PreparedRequest
route(prepared) -> RouteDecision
retrieve(route) -> RetrievalOutcome
evaluate(retrieval) -> EvidenceOutcome
generate(evidence) -> PreparedGeneration
```

### Cách làm chi tiết

1. Tạo `build_rag_runtime(existing_settings)` trong `composition/rag_runtime.py` trước extraction đầu tiên. Runtime bundle dựng `RagExecutor`, retrieval và provider adapter từ `Settings` hiện có; không thêm singleton tạm hoặc service locator.
2. Chụp contract test qua `RagExecutor`: event order, diagnostics mapping, citation attribution, cancellation, deadline, request-local context và budget.
3. Tách từng phase theo thứ tự preparation -> routing -> retrieval -> evidence -> generation; một phase/commit, không di chuyển đồng thời nhiều phase.
4. RED cho observable behavior của phase qua public executor trước khi di chuyển branch tương ứng.
5. GREEN bằng behavior-preserving extraction; không thay prompt, threshold, filter, retry count hoặc feature flag.
6. Phase module chỉ catch exception recoverable cụ thể. `RequestBudgetExceeded` và cancellation phải propagate đến executor; không dùng generic fallback cho control-flow error.
7. Xóa manual budget propagation helper tại caller chỉ sau khi public executor tests chứng minh fail-closed ở router/evidence/planner/decomposition.
8. Thay wildcard imports trong pipeline bằng explicit imports sau từng extraction.
9. Chuyển tests đang monkeypatch private `execute_pipeline` sang scripted executor/public event seam khi chúng chỉ kiểm tra lifecycle. Giữ pure-policy tests cho routing/filter/number logic.
10. `execute_pipeline()` cuối phase chỉ còn ordered composition và construction của result/stream; feature-specific branches nằm trong private phase owner.

### Test và acceptance gate

- Toàn bộ `test_rag_execution_contract.py`, strict stream, RBAC/filter, cache, CRAG, graph, grounded math và decomposition unit tests pass.
- Legacy five-tuple wrapper vẫn giữ call-time behavior và debug-dict mutation contract.
- Sanitized event transcript/OpenAPI không đổi.
- Offline/golden evaluation không regression; provider outage được ghi `inconclusive`, không sửa threshold.
- Concurrency benchmark đạt ngưỡng định lượng trong mục 5; budget/cost/retry invariant giữ nguyên.
- Không feature flag nào đổi default và không release decision nào tự chuyển `accepted`.

### Kết quả mong đợi và điểm debug

- Trace stage map thẳng với preparation/routing/retrieval/evidence/generation, giúp khoanh vùng regression.
- Mỗi phase có typed input/output và reason code; external callers vẫn chỉ biết `RagExecutor`.
- Có thể `git revert` từng phase extraction độc lập; không giữ branch runtime old/new hoặc feature toggle cho refactor.
- Không cần migration hoặc rollout rollback vì observable behavior không đổi.

## Phase 5 — Composition root, config và dependency một chiều

### Mục cần refactor

- Engine, Vision model và RAG singleton được tạo khi import.
- Nhiều module đọc `os.getenv` trực tiếp.
- DB repository còn import RAG logic; RAG còn chạm ingestion/evaluation ở một số đường.
- `registry_ports.py` dùng lazy import/global registry để che dependency ngược.

### Gợi ý refactor

Hoàn thiện composition riêng cho ba process thay vì một global container:

```text
build_app_runtime(settings) -> routers + application modules + adapters
build_rag_runtime(settings) -> RagExecutor + retrieval/provider adapters
build_worker_runtime(settings) -> IngestionRunner + job/adapters
```

Mỗi runtime là frozen dependency bundle được tạo trong lifespan/entrypoint, không phải service locator.

### Cách làm chi tiết

1. Mở rộng frozen Pydantic `Settings` hiện có tại `src/mech_chatbot/config/settings.py`; không tạo source-of-truth thứ hai. Parse/validate environment một lần tại startup và giữ tên/default key hiện tại.
2. Hoàn tất ba composition root đã được tạo dần ở Phase 1, 3 và 4; chuyển các module còn sót sang runtime bundle tương ứng.
3. Application/domain nhận typed dependency qua constructor; adapter nhận config cụ thể, không nhận toàn bộ settings object nếu không cần.
4. Engine/Qdrant/LLM/Vision creation chuyển vào process composition. Import module không được mở connection hoặc load model.
5. Di chuyển pure graph ontology, normalization và registry policy khỏi `rag/`/`ingestion/` sang domain-neutral module để DB có thể dùng mà không import tầng trên.
6. Loại dependency `rag -> evaluation`; activation/governance contract đặt ở neutral governance module và evaluation/RAG cùng phụ thuộc xuống.
7. Thay global lazy registry bằng explicit registration tại composition hoặc pure module trực tiếp; không dùng importlib fallback.
8. Mỗi lần migrate một env key, xóa đường đọc cũ ngay sau parity test; không giữ hai nguồn cấu hình lâu dài.

### Test và acceptance gate

- Import smoke test không load model, không tạo DB connection và không cần external secrets.
- Invalid/missing required setting fail fast tại đúng process startup với sanitized error.
- Concurrent tests không chia sẻ mutated environment/global singleton state.
- Source import graph đạt: `composition -> delivery/application/adapters`, `delivery -> application`, `adapters -> application ports/domain`, `application -> domain`; không có `application -> adapters`, `db -> rag`, `db -> ingestion`, `rag -> evaluation` hoặc `config -> db/rag` callback.
- Startup health/OpenAPI và controlled-demo activation checks giữ nguyên.

### Kết quả mong đợi và điểm debug

- Dependency của mỗi process nhìn được tại một composition root.
- Test thay adapter bằng constructor injection, không monkeypatch module global.
- Config error phân biệt với provider/DB runtime error.
- Rollback bằng `git revert` theo từng process-runtime commit; không thay wire/data contract và không giữ wiring song song.

## Phase 6 — Xóa migration facade và compatibility debt nội bộ

### Mục cần refactor

- `services/__init__.py`, `db/repository.py`, `rag/service.py` và `ingestion/pdf_processor.py` flatten interface bằng dynamic/star re-export.
- Internal callers còn dùng broad legacy import surface.
- Legacy `chat_with_rag` vẫn là compatibility contract có test riêng.

### Gợi ý refactor

Xử lý shim như migration inventory: chuyển internal caller về owner thật, xóa từng internal-only wrapper khi caller bằng 0, nhưng giữ mọi public/unknown compatibility surface cho đến một quyết định deprecation riêng. Không thay namespace phẳng cũ bằng namespace phẳng mới.

### Cách làm chi tiết

1. Inventory bằng `rg` cho từng symbol và phân loại:
   - internal-only shim;
   - test-only shim;
   - public/unknown external compatibility.
2. Migrate internal caller sang explicit module/interface. Chỉ shim được chứng minh `internal-only` mới được xóa khi repo caller count bằng 0 và import smoke/full suite pass. Shim `public/unknown external compatibility` vẫn phải giữ dù repo caller bằng 0, cho đến khi có external inventory và deprecation decision riêng.
3. Xóa dynamic global service re-export; không thay bằng một global application namespace khác.
4. Xóa `db.repository`, `rag.service`, `pdf_processor` chỉ khi được phân loại internal-only và không còn startup/repo caller phụ thuộc; nếu public status chưa rõ thì giữ wrapper mỏng.
5. Mặc định **giữ `chat_with_rag`** vì chính sách tương thích đã chọn. Chỉ xóa bằng một deprecation decision riêng sau external caller inventory; refactor này không tự quyết định xóa.
6. Xóa test implementation-detail gắn với shim sau khi interface-level test thay thế hoàn toàn.
7. Chốt architecture ratchet: không còn allowlist tạm cho dependency đã refactor, không còn wildcard import trong production source ngoài trường hợp được ghi rõ.

### Test và acceptance gate

- `rg` không còn internal import qua shim bị xóa.
- Import smoke cho mọi entrypoint pass.
- Fast/full tests, integration được cấu hình, frontend test/build và OpenAPI diff pass.
- Coverage backend >=80%; changed application modules >=80% line/branch coverage.
- `git diff --check`, stale-term grep và architecture graph pass.

### Kết quả mong đợi và điểm debug

- Import path thể hiện đúng owner của behavior.
- Xóa một module thật sẽ làm complexity quay về owner rõ ràng, không biến mất vì chỉ là pass-through.
- Legacy public behavior vẫn được test qua compatibility facade còn giữ.
- Mỗi shim removal là commit riêng, có thể revert mà không hoàn tác phase trước.

## 5. Validation matrix dùng cho mọi phase

| Gate | Chạy mỗi slice | Chạy cuối phase | Chạy cuối roadmap |
|---|---:|---:|---:|
| RED test đúng seam | Có | Có | N/A |
| Targeted unit/contract tests | Có | Có | Có |
| Architecture ratchet | Có | Có | Có |
| Fast unit + security suite | Không bắt buộc | Có | Có |
| Coverage refactor-owned packages >=80% line/branch | Có | Có | Có |
| Python full suite | Không | Có | Có |
| SQL/Qdrant integration | Không | Bắt buộc nếu phase chạm SQL/Qdrant | Bắt buộc |
| Vue Vitest + build | Khi HTTP contract chạm | Có với API phase | Có |
| OpenAPI + sanitized SSE diff | Khi API/RAG chạm | Có | Có |
| Offline RAG/golden evaluation | Khi RAG behavior chạm | Phase 4 | Có |
| Concurrency/latency benchmark | Không | Phase 4 | Có |
| `git diff --check` | Có | Có | Có |
| Independent code/security review | Không | Có | Có |

Lệnh chuẩn dự kiến:

```powershell
chat_env\Scripts\python.exe -m pytest -m "not integration and not eval" -q
chat_env\Scripts\python.exe -m pytest tests\architecture -q
chat_env\Scripts\python.exe -m pytest tests\unit tests\integration -q --cov=mech_chatbot.application --cov=mech_chatbot.adapters --cov=mech_chatbot.composition --cov=mech_chatbot.rag.phases --cov-branch --cov-report=term-missing --cov-report=json:reports\refactor\coverage-owned.json
chat_env\Scripts\python.exe scripts\quality\check_coverage.py reports\refactor\coverage-owned.json --min-line 80 --min-branch 80
chat_env\Scripts\python.exe -m pytest -q --cov=mech_chatbot --cov-branch --cov-report=term-missing --cov-report=json:reports\refactor\coverage-backend.json
chat_env\Scripts\python.exe scripts\quality\check_coverage.py reports\refactor\coverage-backend.json --min-line 80 --min-branch 80
npm --prefix web-ui run test
npm --prefix web-ui run build
git diff --check
```

Nếu một package đích chưa tồn tại ở phase sớm thì bỏ đúng `--cov=<package>` đó khỏi lệnh intermediate và ghi denominator vào manifest; từ khi package được tạo, nó bắt buộc nằm trong denominator. Final gate luôn dùng toàn bộ `mech_chatbot` và yêu cầu cả line/branch coverage tối thiểu 80%. Integration/evaluation chỉ chạy với service và fixture được cấu hình; skip phải được ghi rõ, không được trình bày như pass. Phase chạm SQL/Qdrant không được `Completed` nếu integration bị skip.

### 5.1. Quy tắc vòng đời và xóa test

Mục tiêu là giữ bộ test đủ mạnh nhưng không tích lũy test trùng lặp theo từng
lớp implementation. Test không bị xóa chỉ vì đã pass. Test chỉ được xóa khi
không còn là hàng rào độc lập hoặc đã có test mạnh hơn thay thế qua seam đích.

Luôn giữ:

- Contract test cho HTTP, SSE, RBAC, schema, typed RAG event và compatibility
  surface còn public/unknown.
- Regression test tái hiện bug, security failure, budget/cancellation và
  fail-closed behavior còn có thể quay lại.
- Architecture ratchet, coverage checker và integration/evaluation gate theo
  phase.
- Characterization test tại interface của deep module nếu đó là bằng chứng duy
  nhất bảo vệ observable behavior.

Có thể xóa:

- Test tạm chỉ phục vụ spike hoặc scaffolding và không bảo vệ behavior sau khi
  slice kết thúc.
- Test implementation-detail của shallow module sau khi test qua interface deep
  module đã bao phủ cùng contract.
- Test trùng lặp cùng seam, cùng input class và cùng failure mode mà không tăng
  khả năng phát hiện regression.
- Test của compatibility shim internal-only sau khi shim và mọi caller đã được
  xóa theo inventory Phase 6.

Gate bắt buộc trước khi xóa test:

1. Ledger ghi mapping `old test -> replacement test` và contract được thay thế.
2. Replacement test phải được chứng minh nhạy với regression tương ứng, không
   chỉ pass trên implementation hiện tại.
3. Targeted suite, architecture gate và full fast suite vẫn pass.
4. Line/branch coverage không giảm; security/compatibility coverage không mất.
5. Xóa test trong cùng commit với replacement hoặc shim removal để rollback độc
   lập. Không dùng số lượng test ít hơn làm lý do duy nhất để xóa.

### 5.2. Artifact và provenance bắt buộc

Mỗi phase ghi evidence dưới cấu trúc sau; artifact là output đã sanitize/canonicalize, không chứa secret hoặc raw confidential content:

```text
reports/refactor/phase-<n>/
  baseline/
    manifest.json
    openapi-app.json
    openapi-rag.json
    sse-success.jsonl
    sse-busy.jsonl
    pytest.txt
  candidate/
    manifest.json
    openapi-app.json
    openapi-rag.json
    sse-success.jsonl
    sse-busy.jsonl
    pytest.txt
    diff-summary.json
```

Chỉ tạo artifact phù hợp với phase, nhưng `manifest.json` luôn bắt buộc và phải ghi: commit SHA, parent/baseline SHA, working-tree status, OS/Python, dependency lock hash, sanitized settings fingerprint, feature-flag snapshot, SQL/Qdrant fixture/snapshot ID, collection, provider/model configuration, concurrency và lệnh đã chạy. Ledger ở đầu tài liệu link thẳng tới artifact và ghi exit code; không chấp nhận dòng mô tả “tests pass” thiếu output/path.

### 5.3. Benchmark RAG Phase 4

Baseline và candidate dùng cùng `scripts/eval/golden_set.jsonl`, SQL/Qdrant snapshot, collection, provider/model config, feature flags, governance scope và concurrency. Khởi động process riêng cho từng arm; không toggle flag trong process đang chạy. Chạy ít nhất ba run hoàn chỉnh mỗi arm với concurrency `1,5,10`, timeout 300 giây:

```powershell
$RefactorBaseUrl = "http://127.0.0.1:8000"
$RefactorEvalUser = "<approved-eval-user>"
$RefactorTrace = "logs\refactor-phase4-candidate-rag-trace.jsonl"
1..3 | ForEach-Object {
  chat_env\Scripts\python.exe scripts\eval\benchmark_rag_concurrency.py scripts\eval\golden_set.jsonl --base-url $RefactorBaseUrl --username $RefactorEvalUser --concurrency 1,5,10 --timeout 300 --trace-jsonl $RefactorTrace --report "reports\refactor\phase-4\candidate\rag-concurrency-run-$_.json"
}
```

Trước lệnh trên, khởi động candidate process với `RAG_TRACE_LOG_FILE=logs\refactor-phase4-candidate-rag-trace.jsonl`; file trace là append-only input cho benchmark, không phải output do script benchmark tạo. Chạy cùng quy trình cho `baseline` trước Phase 4 với trace/report path riêng. Baseline SHA là parent trước extraction, candidate SHA chỉ thêm phase-refactor commit; ngoài source delta đó, data/config phải giống nhau. Raw trace nhạy cảm được giữ local/restricted; chỉ sanitized snapshot/hash và report vào evidence. Gate so median của ba run tại từng concurrency:

- Không giảm success rate và không xuất hiện error/reason code mới.
- P95 first-token và P95 complete của candidate không vượt `baseline * 1.10`.
- P95 của từng stage không vượt `baseline * 1.10`; nếu nghi environment noise, chạy lại cả hai arm với cùng snapshot/config, không miễn gate bằng một run chọn lọc.
- Budget, cost, retry, citation, RBAC và cancellation invariant không đổi.
- Provider outage làm arm `inconclusive`; không dùng run đó trong so sánh và phase chưa được `Completed` cho đến khi đủ ba run hợp lệ mỗi arm.

## 6. Quy tắc xử lý lỗi và rollback

### Khi test đỏ ngoài dự kiến

1. So với baseline/known issue trong ledger.
2. Xác định lỗi ở contract, adapter, environment hay data fixture.
3. Không sửa test nếu observable contract chưa đổi và test đúng.
4. Không mở rộng phase sang feature/algorithm khác để “tiện sửa”.
5. Nếu chưa khoanh vùng trong phase owner, revert phase commit và tái hiện lại từ RED test nhỏ nhất.

### Khi integration/eval lỗi

- `sql_document_missing` hoặc fixture absence là data/preflight blocker, không phải quality regression.
- Provider unavailable/503/429 ngoài policy là `inconclusive`, không tự hạ threshold.
- RBAC, leakage, publication/lifecycle/current-version failure luôn fail closed.
- Không toggle feature flag trong process đang chạy để cứu test; baseline/candidate phải là process/config cô lập.

### Commit/rollback discipline

- Một vertical slice hoặc một private RAG phase trên mỗi commit logic.
- Commit test RED có thể đứng riêng nếu cần review; GREEN phải tham chiếu test đó.
- Không trộn unrelated dirty-worktree changes vào commit refactor.
- Dùng conventional commit: `test:`, `refactor:`, `fix:`, `docs:`.
- Không dùng `git reset --hard` hoặc checkout phá hủy thay đổi; rollback bằng revert của phase commit.
- Không giữ hai implementation old/new, shadow execution hoặc runtime feature toggle chỉ để rollback refactor. Recovery path duy nhất của code là `git revert <phase-commit>` theo thứ tự ngược; data recovery tiếp tục dùng transaction/outbox/restore contract hiện có.

## 7. Definition of Done toàn roadmap

Roadmap chỉ hoàn tất khi tất cả điều kiện sau cùng đúng:

1. FastAPI router và worker loop không sở hữu business orchestration, raw SQL hoặc provider client.
2. Chat, document workflow và ingestion có interface command/result/event rõ ràng, test qua interface đó.
3. `RagExecutor.run()` giữ nguyên contract và `execute_pipeline()` chỉ còn composition của private phases.
4. Dependency graph không còn các cạnh ngược đã liệt kê.
5. Import source không tạo DB/model/provider global ngoài explicit composition root.
6. Pass-through `services` namespace không còn là đường gọi chính; internal shims không cần thiết đã được xóa.
7. Public HTTP/SSE, schema, RBAC, trace, feature flags và rollout decisions không đổi.
8. Architecture tests nằm trong default CI và không còn allowlist cho debt đã xử lý.
9. Backend coverage đạt ít nhất 80%; unit, integration và critical browser/API flows có evidence.
10. Full validation sạch, không có fatal native diagnostic; mọi skip/inconclusive được ghi đúng.
11. Tài liệu này chứa commit/evidence/known-issue/rollback cho từng phase và đã được review độc lập.

## 8. Thứ tự triển khai được khóa

```text
Phase 0 Architecture baseline
    -> Phase 1 ChatTurnRunner
    -> Phase 2 Document and file workflows
    -> Phase 3 IngestionRunner
    -> Phase 4 Private RAG phases
    -> Phase 5 Composition and dependency inversion
    -> Phase 6 Internal shim cleanup
```

Không chạy song song Phase 3–5 vì cùng chạm dependency/config và có nguy cơ conflict. Trong Phase 2, các vertical slice độc lập có thể làm tuần tự trên cùng branch; không merge một slice khi gate của slice trước chưa sạch.

## 9. Execution ledger

### 9.1. Phase 0 — Baseline tin cậy và architecture ratchet

Trạng thái: **Validated / Coverage gate passed; Phase 1 chưa bắt đầu**. Phase 1
chỉ được bắt đầu sau khi review/commit của Phase 0 giữ nguyên toàn bộ evidence
dưới đây.

| Trường evidence | Kết quả thực tế |
|---|---|
| Baseline | SHA `c9a24ac03a022b1f3652dcf62696a57587dd962f`; branch nguồn `codex/p1-retrieval-intelligence`; branch thực hiện `codex/codebase-layer-refactor`; trước implementation chỉ có `docs/codebase-layer-refactor-plan.md` chưa được track. |
| Contract được bảo vệ | Default test collection; dependency một chiều; HTTP/OpenAPI; thứ tự SSE success/busy; upload/review response; typed `RagExecutor`; native import health. Không thay đổi RAG algorithm, feature flag hoặc rollout decision. |
| RED | Architecture test đỏ khi chưa có allowlist; native sentinel bắt `Windows fatal exception: access violation` dù subprocess trả `0`; coverage/evidence/capture module đỏ vì chưa tồn tại; regression OpenAPI đỏ khi sanitizer làm mất password route/schema. |
| GREEN | Commit `3b663f423e90b9a5dc3aa1df9900d86e5cc1bf7d` thêm architecture ratchet, native sentinel/fix, coverage checker, canonical evidence và sanitized baseline artifacts. Commit review-fix `d6076b2` làm native preload fail-fast khi installation hỏng và tách các scanner/validator dài thành helper nhỏ. Commit `55b7149` thay SSE fixture tĩnh bằng transcript quan sát qua endpoint thật với system-boundary fakes; `e67cc89` tách contract checks thành test nhỏ dưới 50 dòng. |
| Validation | Baseline gate và architecture tests pass; full suite sau Wave 6 có **1.891 passed, 22 skipped**, 1 Starlette/httpx warning; default collect 1.913 tests; architecture suite 8 pass; OpenAPI/SSE contract tests pass; `git diff --check` pass; không còn fatal native diagnostic. SQL/Qdrant/RAG server skips giữ nguyên điều kiện opt-in và được ghi rõ. |
| Coverage | Sau Wave 6: 14.362/16.808 statement = **85,447406% line**; 4.108/5.120 branch = **80,234375% branch**. `check_coverage.py --min-line 80 --min-branch 80` pass. Wave 5 trước đó là 13.896/16.808 và 3.882/5.120; toàn bộ delta được ghi ở ledger Wave 5/6. |
| Architecture delta | Chưa xóa debt trong Phase 0. Baseline ratchet có 264 identity và 392 occurrence; mọi occurrence tăng thêm hoặc allowance bị stale đều làm test fail. |
| Known issues | SQL/Qdrant integration thật chưa được cấu hình; Vue coverage hiện chỉ là report baseline 21,94% line/14,66% branch, chưa có threshold 80%; còn `StarletteDeprecationWarning` về `httpx`/`TestClient`; `chat_env` có dependency drift so với lock đã ghi ở baseline. Backend coverage CI 80/80 đã được bật. |
| Rollback | Behavior/evidence rollback theo thứ tự `git revert e67cc89`, `git revert 55b7149`, `git revert d6076b2` rồi `git revert 3b663f423e90b9a5dc3aa1df9900d86e5cc1bf7d`. Các docs-only commit `714821e`, `bfb922c` và ledger amendment về sau được chủ ý giữ làm audit trail; chúng không thay đổi runtime behavior. |

Review hai trục: ba standards finding đã được sửa trong `d6076b2`. Spec review
xác nhận không có scope creep; SSE transcript gap đã được đóng trong `55b7149`.
Phase 0 vẫn thiếu coverage/CI gate và mục này được giữ fail-closed trong
`Known issues`, không được diễn giải thành phase hoàn tất.

Artifact chuẩn:

- `reports/refactor/phase-0/baseline/manifest.json`, SHA-256
  `5492469f2a36852fa6454e2a4b6be9aa3cda61e087804504088797badf76e994`.
- `reports/refactor/phase-0/baseline/openapi-app.json` và
  `openapi-rag.json` là OpenAPI đã canonicalize nhưng giữ nguyên route/schema.
- `reports/refactor/phase-0/baseline/sse-success.jsonl` và
  `sse-busy.jsonl` là transcript đã sanitize, được quan sát qua endpoint thật
  `/api/chat/message` với RAG/persistence/audit/vision system-boundary fakes.
- `reports/refactor/phase-0/baseline/upload-review-samples.json` khóa shape
  upload và pending-publication review.
- `reports/refactor/phase-0/baseline/pytest-baseline.txt` ghi denominator và
  trạng thái fast suite. Coverage JSON thô và provenance input nằm trong vùng
  ignored; chỉ artifact đã sanitize được track.

#### Coverage hardening Wave 1

Wave 1 chỉ thêm characterization test, không sửa production/backend code. Sáu
suite mới được phân loại là **giữ lâu dài** cho đến khi seam mới tương ứng thay
thế đầy đủ:

| Test mới | Contract được khóa | Điều kiện xóa về sau |
|---|---|---|
| `test_supported_file_readers.py` | Public dispatch và fail-closed behavior của supported-file readers | Chỉ sau khi `IngestionRunner`/reader port có contract test thay thế cùng format và failure mode. |
| `test_pdf_metadata.py` | `extract_metadata_smart` regex-first, LLM merge/fallback | Chỉ sau khi metadata extractor port mới thay thế đủ regex, merge và provider failure. |
| `test_ingestion_worker.py` | Worker poll/reconcile/finalize/quality-gate qua system-boundary fakes | Chỉ sau khi `IngestionRunner` và worker adapter test thay thế cùng job-state transitions. |
| `test_vision_client.py` | Vision provider boundary, image encoding, throttle, cache và error policy | Giữ như adapter contract; chỉ xóa case thật sự trùng khi adapter mới có cùng input/failure class. |
| `test_publication_validation.py` | Publish contract/actor validation fail-closed | Giữ cùng SQL integration test; unit fake không thay thế transaction semantics. |
| `test_rag_entity_conversation_characterization.py` | Entity resolution, continuation, dominant refs và history summary | Chỉ sau khi RAG phase/runner mới bảo vệ cùng observable behavior. |

Review phát hiện các case mới về candidate selection/context/description trùng
`tests/test_conversation_state.py`; các case trùng đã bị bỏ khỏi file mới trước
commit, còn test cũ được giữ. Vì vậy Wave 1 không xóa test tracked nào và không
cần mapping `old -> replacement`. Các suite cũng cố định environment liên quan
để không phụ thuộc cấu hình deployment của máy chạy test.

Evidence sau review fix: 108 test Wave 1 pass; hostile-environment run cho
metadata/worker/vision/RAG pass; full suite tuần tự pass với 22 skip được ghi
rõ. Coverage tăng từ 8.818/16.808 lên 9.535/16.808 statement và từ
2.085/5.120 lên 2.429/5.120 branch. Tương ứng **56,728939% line** và
**47,441406% branch**; checker 80/80 vẫn trả exit `1` đúng thiết kế. Còn thiếu
3.912 statement và 1.667 branch để chạm threshold; Phase 1 tiếp tục bị chặn.

#### Coverage hardening Wave 2

Wave 2 tiếp tục chỉ thêm contract/characterization test, không sửa production.
195 test mới pass cùng nhau; full suite tuần tự pass với 22 skip đã biết.

| Test mới | Contract được khóa | Vòng đời |
|---|---|---|
| `test_catalog_repository.py`, `test_doc_metadata_repository.py` | Catalog lifecycle/RBAC/Qdrant rollback và document metadata validation/mapping | Giữ đến khi repository port mới có cùng result, mutation và fail-closed contract. |
| `test_feedback_repository_contract.py`, `test_knowledge_governance_repository_contract.py` | Feedback/golden/regression flow và governance validation/audit/cache | Giữ; không thay thế SQL/Qdrant integration semantics. |
| `test_jobs_repository.py` | Ingestion job create/pick/update/quota/cancel/requeue/ETA | Giữ cùng worker test; chỉ xóa khi job repository port và worker adapter cùng thay thế state transitions. |
| `test_pdf_vision.py` | Vision response parsing/formatting và opt-in prewarm cache | Giữ đến khi vision adapter/prewarm port mới bảo vệ cùng no-op, cache và best-effort behavior. |
| `test_semantic_cache_public_contract.py` | Exact/semantic lookup, provenance, stale record, store và stream lifecycle | Giữ như security/cache contract. |
| `test_rag_server_endpoints.py` | Service auth, server-side RBAC, `/chat`, SSE, history/save/feedback | Giữ như HTTP/SSE contract; không xóa các auth/cancellation test cũ. |

Không có test tracked nào bị xóa trong Wave 2 và review không tìm thấy case
mới trùng đủ để thay thế test cũ, ngoại trừ stream-failure case được xử lý theo
mapping sau. Assertions raw exception đã bị loại để không đóng băng việc lộ chi
tiết nội bộ thành public contract; chat và stream đều gửi RBAC field độc hại rồi
xác nhận server-side profile thắng.

| Test cũ đã xóa | Replacement mạnh hơn | Contract |
|---|---|---|
| `test_semantic_cache_stream_lifecycle.py::test_partial_or_cancelled_stream_is_never_saved_to_semantic_cache` | `test_semantic_cache_public_contract.py::test_failed_stream_propagates_error_without_storing` và `::test_cancelled_stream_does_not_store_partial_answer` | Error phải propagate, cancellation/close và stream chưa hoàn tất không được ghi partial answer; replacement đi qua repository boundary và tách hai failure mode. |

Các case gọi private helper trong catalog/doc-metadata/PDF-vision chỉ là
characterization **tạm thời**: khi public repository/vision port mới cover cùng
input class và failure mode, ledger phải map từng case sang replacement rồi xóa
case private-detail trong chính commit thay thế. Các public route/repository
contract còn lại là test giữ lâu dài.

Coverage sau review Wave 2: 10.818/16.808 statement = **64,362208% line** và
2.849/5.120 branch = **55,644531% branch**. So với Wave 1 tăng 1.283 statement
và 420 branch. Checker 80/80 vẫn trả exit `1`; còn thiếu 2.629 statement và
1.247 branch nên Phase 1 tiếp tục bị chặn.

Known issues không được đóng băng thành contract:

- `semantic_cache.lookup_exact()` có thể raise nếu record cache chứa
  `est_cost` sai định dạng thay vì fail closed; `source_doc_ids` là JSON hợp lệ
  nhưng không chuyển được sang integer có thể tạo danh sách normalize rỗng và
  bỏ qua freshness verification.
- Error path hiện tại của RAG HTTP/SSE có thể chứa tên exception và message
  provider. Wave 2 chỉ assert status/event shape, không assert raw detail; việc
  sanitize cần một security fix TDD riêng có phê duyệt thay đổi behavior.
- Department guard của `create_ingestion_job()` đang fallback cho mọi
  SQLAlchemy `OperationalError`, chưa phân biệt legacy missing-schema với outage
  thật. Test fail-open đã bị loại; cần security/reliability fix TDD riêng trước
  khi coi legacy fallback là contract.
- `/chat/feedback` hiện bỏ qua boolean trả về từ persistence và luôn trả
  `{"ok": true}`. Test success dùng boundary trả `True`; failure response chưa
  được đóng băng và cần application/API fix riêng.
- Các fake SQLAlchemy protocol đang lặp giữa repository test files; chỉ tách
  shared test helper nếu chứng minh không làm mất fidelity hoặc che boundary
  mismatch. Không xóa suite chỉ để giảm số dòng.

#### Coverage hardening Wave 3

Wave 3 thêm 197 test behavior/contract ở `ui_queries`, document/chat,
access/document-pages, RAG context/intent, PDF ingestion và `app_server`. Không
sửa production. Các test mới pass cùng nhau; full suite tuần tự pass với 22
skip đã biết.

Test HTTP/SSE, RBAC, repository public result và ingestion public scenario là
test giữ lâu dài. Test gọi `_get_or_create_doc`, `_reingest_snapshots`, table
name/SQL row order và orchestration helper hiện tại là characterization tạm;
phải xóa theo mapping khi repository/runner port mới bảo vệ cùng behavior.

| Test cũ đã xóa | Replacement mạnh hơn | Contract |
|---|---|---|
| `test_common_metadata_context.py::test_common_metadata_context_renders_title_without_key_error` | `test_context_builders_contract.py::test_common_metadata_renders_all_supported_fields_and_expired_status_warning` và `::test_common_metadata_warns_for_past_expiry_but_tolerates_unparseable_dates` | Cùng public seam: test đầu khóa title/doc-number và toàn bộ supported fields; test thứ hai dùng metadata sparse để chứng minh optional field thiếu không gây `KeyError`. Full coverage không giảm vì cả hai replacement chạy trong cùng suite. |

Review đã loại các assertion/case có thể đóng băng raw upstream error,
malformed approval, unaudited privilege mutation, clearance fallback elevated
và snapshot-then-delete khi snapshot đọc lỗi. Các behavior production tương
ứng được giữ thành blocker, không được diễn giải là contract đã chấp nhận:

- bulk publication và upstream SSE có thể trả chi tiết lỗi nội bộ;
- reset document children có thể tiếp tục xóa sau khi một child snapshot đọc
  lỗi, làm rollback thiếu dữ liệu;
- access repository có thể trả raw DB error; wrapper HTTP có nguy cơ báo outer
  success khi inner result lỗi;
- malformed access request có thể được approve với `applied=None`; privilege
  mutation có thể thành công dù audit persistence lỗi;
- `get_user_clearance()` fallback `internal` khi DB outage thay vì least
  privilege;
- một số document lifecycle write nuốt lỗi và trả `None`, caller không phân
  biệt success/no-op/failure; `_get_or_create_doc` là exported legacy seam nhưng
  mang tên private.

Coverage sau review Wave 3: 12.274/16.808 statement = **73,024750% line** và
3.338/5.120 branch = **65,195312% branch**. Checker 80/80 vẫn exit `1`; còn
thiếu 1.173 statement và 758 branch. Phase 1 tiếp tục bị chặn.

#### Coverage hardening Wave 4

Wave 4 thêm 140 test cho RAG pipeline/steps, app endpoints, PDF resilience,
publication workflow và năm repository nhỏ; không sửa production. Full suite
pass với 22 skip. Coverage đạt 13.264/16.808 statement = **78,914802% line** và
3.600/5.120 branch = **70,312500% branch**. Checker 80/80 vẫn exit `1`; còn
thiếu 183 statement và 496 branch nên Phase 1 tiếp tục bị chặn.

`test_pipeline_steps_orchestration.py` và các fake repository chi tiết là
characterization tạm, phải xóa sau khi public `RagExecutor.run`/repository port
test thay thế cùng behavior. Review loại assertion chấp nhận invalid cache doc
IDs và đổi fixture reconciliation sang policy `internal_only`, không đóng băng
fallback `all_external` khi policy thiếu. Missing external-processing policy
vẫn là security decision cần fail-closed fix riêng.

Điều kiện gỡ blocker trước Phase 1: bổ sung characterization test để toàn bộ
`mech_chatbot` đạt tối thiểu 80% line và branch như kế hoạch hiện tại, hoặc có
quyết định sửa chính sách gate thành coverage 80% cho package refactor-owned
kèm global no-regression ratchet. Không được tự hạ threshold trong code hay CI.

#### Coverage hardening Wave 5

Wave 5 thêm 135 test contract/characterization, không sửa production code. Các
test mới chạy qua HTTP/SSE, `DefaultRagExecutor`, public RAG policy, ingestion
extractor và system-boundary fakes. Full suite tuần tự pass với 22 skip đã biết.

| Test group | Contract và vòng đời |
|---|---|
| `test_app_server_wave5_contracts.py` | HTTP auth/session, feedback, image ownership, history/citations, access administration và upload/health. Contract HTTP giữ lâu dài; fake database/provider chỉ là system boundary. |
| `test_rag_pipeline_wave5_characterization.py` | Exact/semantic cache fallback và context-history bypass qua `DefaultRagExecutor.run`. Giữ đến khi cache/retrieval port mới thay thế cùng lifecycle contract. |
| `test_wave5_rag_public_boundaries.py` và năm test pure RAG | Typed event/budget/SSE, context, grounded math, regression, intent/router/glossary/rerank. Public policy/HTTP cases giữ lâu dài; private policy helpers chỉ giữ đến khi deep seam mới bao phủ cùng failure mode. |
| `test_wave5_document_classifier_characterization.py`, `test_wave5_material_registry_characterization.py`, `test_wave5_file_access_security.py`, `test_wave5_pdf_chunking_characterization.py` | Reader/classifier/registry/file-access/chunking behavior. Public extraction/security cases giữ; parser/tokenizer/private helper cases là characterization tạm và phải map sang ingestion port trước khi xóa. |
| `test_pipeline_steps_remaining_branches.py` | Legacy orchestration characterization. Loader đã chuyển về module chuẩn `mech_chatbot.rag.pipeline_steps` để coverage đo đúng; history/retrieval/rewrite/stream/citation cases là tạm cho đến khi `RagExecutor.run` thay thế. |

Không xóa test tracked nào trong Wave 5. Review đã sửa các case có nguy cơ
đóng băng feedback false-success, raw provider error và replay expiry chưa
được thực thi; không biến các behavior đó thành contract.

Coverage sau Wave 5: 13.896/16.808 statement = **82,674917% line** và
3.882/5.120 branch = **75,820312% branch**. Line gate đã đạt nhưng branch gate
còn thiếu 214 nhánh, nên Phase 1 tiếp tục bị chặn.

#### Coverage hardening Wave 6

Wave 6 bổ sung 56 test branch-focused, không sửa production code. Ngoài ra
Wave 5 legacy suite được chỉnh loader/fake để coverage đo đúng; không tính các
case đó là test mới. Mục tiêu là
các nhánh còn thiếu qua public seam; không giữ test cho raw error leakage,
audit-failure success, malformed approval hoặc missing-policy elevated access.

| Test group | Contract và vòng đời |
|---|---|
| `test_wave6_api_branch_contracts.py` | App/RAG HTTP và SSE branch contracts qua `TestClient`; giữ lâu dài cùng OpenAPI/SSE/RBAC surface. |
| `test_wave6_pdf_ingestion_branches.py` | `extract_metadata_smart`, markdown/BOM và PDF pipeline public extraction; giữ durable; lazy/parser helper cases là tạm. |
| `test_wave6_repository_branch_contracts.py` | Community summaries, graph, material, external AI, lifecycle và rollout qua repository public seams với SQL/Qdrant fakes; giữ đến khi repository port/integration thay thế cùng semantics. |
| `test_wave6_rag_eval_branch_contracts.py` | Pure RAG/evaluation policy, schema, grounding, decomposition và failure-family branches; giữ như policy contract, không dùng để phê duyệt rollout live. |
| `test_pipeline_steps_remaining_branches.py` (canonical loader) | Giữ 16 case legacy orchestration sau khi fake strict/broad retrieval được sửa để thực sự chạy fallback; xóa khi `RagExecutor.run` cover cùng lifecycle. |

Wave 6 targeted suite pass; full suite pass với **1.891 passed, 22 skipped**
(skip cần SQL/Qdrant/RAG server/late-interaction opt-in). Coverage thực tế sau
full run: 14.362/16.808 statement = **85,447406% line** và 4.108/5.120 branch =
**80,234375% branch**. `scripts/quality/check_coverage.py --min-line 80
--min-branch 80` pass. So với Wave 5 tăng 466 statement và 226 branch; chênh
lệch thấp hơn tổng delta đo riêng vì nhiều arc được các test khác bao phủ.

#### Phase 0 gate enablement

`.github/workflows/tests.yml` giờ chạy Python fast suite kèm `coverage.py`
branch report và `check_coverage.py` 80/80; frontend chạy `vitest` coverage
report và build. Local frontend evidence: 32 test pass, build pass, coverage
report 21,94% line và 14,66% branch. Vue coverage hiện là baseline quan sát
(chưa đặt threshold 80% trong plan); backend global line/branch gate mới là
điều kiện chặn Phase 1.

Phase 0 không thay đổi feature flag, rollout decision, RAG algorithm hay API
runtime. Known issues còn lại: SQL/Qdrant integration thật chưa được cấu hình,
warning Starlette/httpx và dependency drift trong `chat_env`; các mục này vẫn
được ghi là skip/diagnostic, không trình bày như pass.

### 9.2. Phase 1 — `ChatTurnRunner`

Trạng thái: **Completed / Validated**. Phase 1 không thay đổi feature
flag, rollout decision, RAG algorithm, database schema hay HTTP/OpenAPI/SSE
wire contract.

| Trường evidence | Kết quả thực tế |
|---|---|
| Baseline | Commit `9dd2576` (`test: close phase zero coverage gate`), branch `codex/codebase-layer-refactor`. Trước Phase 1, Phase 0 đã pass global backend gate; SQL/Qdrant/RAG server vẫn là opt-in skip. |
| Contract được bảo vệ | `POST /api/chat/message` vẫn giữ Pydantic body, HTTP 200 streaming response, event order `thinking -> delta* -> citation* -> done` hoặc `thinking -> error`; RAG payload, server-resolved actor, CSRF/image ownership, service-token headers, citation URLs, persistence/audit semantics và pilot replay/drop behavior được giữ. OpenAPI app/RAG canonicalized bằng baseline Phase 0 đều bằng nhau. |
| RED | `tests/unit/test_chat_turn_runner.py` khóa success aggregation, attribution, persistence/audit, busy, incomplete stream, persistence warning và pilot replay/drop. `tests/unit/test_chat_runtime_adapters.py` khóa HTTP/SSE mapping, transport error, non-2xx cleanup, repository/audit adapters. `tests/unit/test_app_runtime_composition.py` khóa frozen runtime. Các test endpoint ban đầu còn phụ thuộc monkeypatch global; review dùng seam đó làm blocker và đã chuyển sang scripted runner. |
| GREEN | Thêm `application/chat_turn.py`, `application/chat_citations.py`, `adapters/chat_runtime.py`, `composition/app_runtime.py`; router chỉ tạo command/actor và serialize typed events. Production wiring nằm trong `build_default_app_runtime()`; `app.state.runtime` là frozen bundle, không có dual implementation hay runtime toggle. Lỗi persistence/transport được log server-side nhưng SSE chỉ nhận thông báo tổng quát; security-level thiếu/sai được audit fail-closed. |
| Validation | Targeted Phase 1/API/architecture suite: pass. Full collection `1.930` tests; full run `1.908 passed, 22 skipped, 0 failed`, 1 `StarletteDeprecationWarning` đã biết. `git diff --check` pass. `scripts/quality/check_coverage.py reports/refactor/phase-1/coverage-backend.json --min-line 80 --min-branch 80` pass. |
| Coverage | Full backend sau Phase 1: `15.317/17.144` statements = **89,343210% line**; `4.285/5.164` branches = **82,978311% branch**. Module-owned gate: adapters 93%, application citation 97% (full suite; targeted 93%), ChatTurnRunner 97%, composition 100%. |
| Architecture delta | Xóa dependency `direct_getenv` mới khỏi adapter bằng cách để composition truyền config; API không còn import concrete adapter. Citation attribution nằm trong application module. Architecture suite `8 pass` và không thêm violation/allowlist. |
| Test lifecycle | `test_app_chat_orchestration.py` đã bỏ test pilot endpoint phụ thuộc transport/repository monkeypatch vì runner/adapter contract đã thay thế; giữ queue-boundary test và thin endpoint SSE tests. Không xóa test contract nào nếu chưa có replacement; các suite runner/adapter/composition là durable cho đến khi seam tương ứng thay đổi. |
| Known issues | SQL/Qdrant/RAG integration thật chưa chạy; 22 skip giữ nguyên opt-in. `StarletteDeprecationWarning` về `httpx`/`TestClient` còn mở. `chat_env` dependency drift so với lock vẫn là diagnostic. Full backend coverage pass nhưng một số module legacy riêng lẻ dưới 80%; đây là debt ngoài Phase 1 và không hạ global gate. |
| Rollback | Revert `97e4183` (`refactor: deepen browser chat into turn runner`) rồi revert commit ledger docs nếu cần; chỉ có một implementation chat. Không cần data/schema rollback vì không có migration hoặc thay đổi persistence contract. |

Review hai trục đã chạy sau GREEN. Các finding về audit fail-open, raw error
leakage, response cleanup, composition placement, citation ownership và test
seam đã được sửa; targeted suite và full suite chạy lại sau tất cả sửa đổi.

### 9.3. Phase 2 — Document/file workflow owners

Trạng thái: **Completed / Validated**. Toàn bộ code-side gate, router extraction,
OpenAPI, coverage, frontend và SQL publication/outbox integration đã đạt. SQL
test chạy trên database demo chính theo phê duyệt của người dùng, tạo dữ liệu
tạm có định danh ngẫu nhiên và xác nhận cleanup về 0 record sau test.

| Trường evidence | Kết quả thực tế |
|---|---|
| Baseline | Commit `2c413a5` (`docs: close phase one refactor ledger`), branch `codex/codebase-layer-refactor`. Trước Phase 2, full suite Phase 1 pass với `1.908 passed, 22 skipped`; working tree sạch. |
| Contract được bảo vệ | Auth/CSRF/RBAC cho upload, review, publish và protected files; response shape upload `{ok, job_id, file_name}` và batch `{ok, jobs, errors, created, failed}`; bulk review `{ok, updated, pending, failed, failures}`; publication outbox mapping `published`/pending; protected file traversal/dot-file/chat-image ownership fail closed; no-idempotency upload behavior. |
| RED | Thêm application/adapter/HTTP characterization mới: `tests/unit/test_protected_files_application.py`, `tests/unit/test_protected_files_adapters.py`, `tests/unit/test_document_operations_application.py`, `tests/unit/test_document_runtime_adapters.py`, cùng endpoint contract mở rộng trong `test_app_server_endpoint_characterization.py`, `test_app_server_remaining_endpoint_contracts.py`, `test_app_server_wave5_contracts.py` và `test_wave6_api_branch_contracts.py`. RED cuối cùng tái hiện việc `knowledge_approver` qua HTTP reviewer gate nhưng bị application layer từ chối; implementation sau đó dùng chung `role_allows()` để giữ capability mapping cũ. |
| GREEN | Commit `4e165b7` thêm ba application owner, protected-file/upload/publication adapters, runtime wiring và test đầu tiên. Commit `ccd6814` hoàn tất router split, đưa pilot replay sang process adapter, giữ compatibility wrapper/signature cũ, chuyển graph script/test sang owner mới, đưa authorization/publication orchestration lên application layer, dùng atomic upload và xóa `sys.modules` service locator. `api/app_server.py` giảm từ 2.661 còn 548 dòng và chỉ đăng ký health route; route nghiệp vụ nằm trong `api/routers/chat.py`, `documents.py`, `operations.py`. |
| Validation | Candidate artifact: `reports/refactor/phase-2/candidate/manifest.json`; pytest evidence: `candidate/pytest-baseline.txt`; SQL evidence: `candidate/sql-publication-integration.txt`; diff: `candidate/diff-summary.json`; coverage thô local: `reports/refactor/phase-2/coverage-backend.json`. Full backend exit `0`: **1.959 passed, 22 skipped, 0 failed**, 1 warning. Coverage checker exit `0`: **85,675812% line**, **80,088326% branch**. Targeted Phase 2/API/architecture exit `0`; architecture **8 passed**. OpenAPI candidate bằng chính xác Phase 1 baseline: **116 paths / 125 operations**; SSE app/RAG capture hashes không đổi. `npm test -- --run` exit `0`: **11 files / 32 tests**; `npm run build` exit `0`; `git diff --check` exit `0`. `RUN_DB_TESTS=1 ... test_publication_outbox_sql.py` exit `0`: **2 passed** trên SQL demo; post-test check xác nhận **0** department/user/document fixture còn lại. |
| Architecture delta | Xóa toàn bộ Phase 2 allowlist cho direct data access ở `api/app_server.py`/`api/file_access.py`; app server và feature router không import `db.engine`, `db.repositories`, `sqlalchemy` hoặc raw SQL. Browser API không còn import function qua flat root `services` namespace; từng feature service module được import tường minh. Flat compatibility export còn lại trong `services/__init__.py` và caller `rag_server.py` thuộc debt Phase 6/phase khác, không mở rộng trong Phase 2. |
| Known issues | Các SQL/Qdrant/eval suite ngoài acceptance scope Phase 2 vẫn opt-in. Còn 1 `StarletteDeprecationWarning` về `httpx`/`TestClient`. Security review ghi nhận protected-file HTTP response vẫn phân biệt một số lý do từ chối (`security_denied`, `not_found`...); đây là observable contract đã được characterization khóa, nên không đổi trong structural refactor. Việc tổng quát hóa thông báo cần security behavior decision riêng. Không có schema/data migration hoặc rollout/feature-flag change. |
| Rollback | Revert `ccd6814` rồi `4e165b7`; revert commit evidence/ledger sau cùng nếu muốn bỏ audit trail. Không có schema/data rollback. Publication outbox vẫn là recovery path; compatibility export chỉ giữ một implementation và sẽ xử lý ở Phase 6. |

Test direct-call cũ `test_bulk_publish_returns_pending_without_marking_job_published`
đã được xóa khỏi `tests/unit/test_app_chat_orchestration.py`. Replacement là
`test_bulk_publish_preserves_published_pending_and_failed_accounting`,
`test_publication_adapter_resolves_job_document_and_marks_only_published` và
`test_ingestion_publish_marks_job_only_after_published_transition`; ba test này
khóa cùng behavior ở application, adapter và HTTP seam. Không xóa test contract
nào khác trong Phase 2.

Review cuối: standards PASS sau khi sửa capability role; spec không còn
code-side blocker và xác nhận flat root service cleanup còn lại đúng Phase 6.
Security review không có Critical/High, nhưng có một Medium follow-up về độ chi
tiết response protected-file đã giữ nguyên theo compatibility policy. Code
candidate là `ccd6814`; SQL closure evidence được capture trên evidence base
`1e12a3d`, với toàn bộ working-tree status trước commit được liệt kê trong
manifest. Settings/feature flags đã sanitize và không chứa secret. SQL target
chỉ được ghi dưới dạng SHA-256; fixture `draft_doc` là ephemeral, hai integration
test pass và cleanup đã được xác nhận độc lập.

### 9.4. Phase 3 — Ingestion lifecycle owner

Trạng thái: **Completed / Validated với ngoại lệ P2 được chấp nhận**. Toàn bộ
behavior, security, integration và global backend gate đã pass. Ngày
2026-07-22, người dùng chấp nhận ngoại lệ cho hai legacy RAG monolith được chạm
một dòng fail-closed nhưng vẫn dưới ngưỡng coverage từng module. Threshold 80/80
không bị hạ và bằng chứng strict all-touched vẫn được giữ trong artifact.

| Trường evidence | Kết quả thực tế |
|---|---|
| Baseline | Commit `1447d3c` (`docs: close phase two integration gate`), branch `codex/codebase-layer-refactor`; working tree sạch trước Phase 3. Code candidate là `387ee9e` (`refactor: create ingestion lifecycle owner`). |
| Contract được bảo vệ | Worker vẫn claim một job, reconcile publication/serving, giữ backoff 5/10 giây và các final status/report cũ. Public `learn_new_file`, `process_and_ingest_pdf` và `process_and_ingest_file` giữ explicit signature, return tuple/report schema và legacy progress adapter. Không có schema migration, endpoint/OpenAPI change, feature toggle hay thay đổi success-path có chủ ý. |
| RED | Runner tests khóa success, pending review, quality blocked, report persistence fail-closed, quota từ report lẫn raised exception và unexpected failure. Adapter tests khóa PhongBan normalization, parameterized SQL, typed progress mapping và governed classifier. Pipeline tests khóa compatibility signature, typed progress, rollback sau vector/SQL/Qdrant failure, missing document identity/governance, non-PDF external context và quality-block rollback. Security regression khóa policy thiếu/NULL ở governance, publication, evidence verifier, claim repair, generation và rerank thành `internal_only`. |
| GREEN | Thêm `application/ingestion_runner.py`, `adapters/ingestion_runtime.py`, `composition/worker_runtime.py`, `config/worker_settings.py` và `ingestion/progress.py`. Worker chỉ claim/reconcile/delegate/log/backoff; lifecycle outcome và transition thuộc runner/store. `ingestion/pdf/pipeline.py` trở thành compatibility wrapper, implementation duy nhất nằm ở `pipeline_implementation.py`; các phase source/report/finalize/rollback dùng immutable context helper. |
| Typed protocol | `IngestionProgressEvent` dùng các phase `classifying`, `extracting`, `embedding`, `quality_check`, `completed`. Store adapter map về vocabulary SQL cũ; legacy callback map ngược về message/sentinel cũ nên không mở dual implementation. |
| Security delta | Missing/inactive governance, lookup failure hoặc policy NULL đều fail closed thành `internal_only`; direct/CLI classifier mặc định không gọi external. Chỉ worker adapter sau khi đọc governance active và explicit `all_external` mới opt-in classifier external. Quality blocked, SQL classification sync failure hoặc Qdrant payload sync failure đều rollback và không clear snapshot. Đây là thay đổi error/missing-policy path có chủ ý; explicit `all_external` success path không đổi. Security reviewer final: PASS, không còn finding actionable. |
| Validation | Artifact: `reports/refactor/phase-3/candidate/`. Full suite kèm branch coverage exit `0`: **2.008 passed, 22 skipped, 0 failed** trên **2.030 collected**, 1 `StarletteDeprecationWarning` đã biết. Architecture **8 passed**. Test-order reproduction `strict_stream_guard -> public RAG` **15 passed** sau khi restore module cache. `git diff --check` exit `0`. Standards reviewer final: PASS. |
| Coverage | Full backend `coverage-backend.json`: **86,191868% line / 80,624286% branch**, checker 80/80 pass. Phase 3 owned + security-small `coverage-owned.json`: **93,223140% line / 87,537994% branch**, checker pass; từng core Phase 3 module đạt >=80 line/branch. `coverage-touched.json` cố ý giữ bằng chứng strict all-touched: **67,894103% / 61,896243%** do `rag/pipeline.py` và loader-driven `rag/pipeline_steps.py` là legacy monolith; các dòng policy thay đổi có regression trực tiếp nhưng literal per-touched-module gate chưa pass. |
| SQL/Qdrant integration | Chạy read-only trên database demo chính theo phê duyệt người dùng, collection `TaiLieuKyThuat_v2`, snapshot `phase3-ingestion-consistency-snapshot-v1` ghim 18 upload document theo DocID/file name. Kết quả **2 passed**: mọi SQL document vectorized có Qdrant point và payload file/domain/security/department khớp SQL. Không tạo fixture, ingest, re-ingest hoặc xóa dữ liệu. |
| Test lifecycle | Không giữ test đo lường tạm ngoài acceptance evidence. Các test runner/adapter/runtime/wrapper/security là durable contract tests. Test strict-stream được sửa isolation thay vì xóa vì nó bảo vệ fail-closed generation và từng tái hiện pollution theo thứ tự suite. |
| Known issues / closure | P2 duy nhất: strict wording `changed module coverage >=80% line/branch` chưa đạt cho hai legacy RAG monolith bị chạm bởi security fail-closed. Global backend và Phase 3-owned gate đều pass; không hạ threshold. Còn 1 warning `httpx`/`TestClient`; 22 integration/eval skip giữ nguyên opt-in và không được tính là pass. |
| Rollback | Revert code candidate `387ee9e`, rồi revert commit evidence/ledger. Không có schema/data rollback. Sau revert, cần lưu ý các missing-policy fallback `all_external` cũ sẽ quay lại; vì vậy rollback production chỉ nên thực hiện khi đồng thời có security mitigation tương đương. |

Ownership review xác nhận production path là `worker -> IngestionRunner ->
IngestionProcessor port -> single pipeline implementation`; compatibility
entrypoint không tự tạo runner vì thiếu job/store identity nhưng chỉ forward,
không chứa implementation thứ hai. Spec review không còn P0/P1 correctness hay
security blocker; chỉ giữ P2 coverage exception nói trên. Ngoại lệ được chấp
nhận rõ ràng ngày 2026-07-22 nên Phase 4 được phép mở; đây không phải tuyên bố
hai legacy module đã đạt per-module 80/80.
