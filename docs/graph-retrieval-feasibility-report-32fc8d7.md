# Graph Retrieval Feasibility Report — `32fc8d7`

Trạng thái: khuyến nghị `keep_off_technical_limit`; chưa được chấp nhận. Báo cáo này là supporting evidence, không phải formal evidence, pilot authorization hay live authorization.

## Phạm vi

Đánh giá xem Graph Retrieval có thể tiếp tục sang formal series mà vẫn giữ nguyên các gate đã khóa: không provider/retrieval error, fallback hoặc retry; latency ratio không vượt ngưỡng; không nới provenance, RBAC, safety hoặc quality oracle.

## Bằng chứng đã khóa

| Cửa sổ | Prerequisite | Kết quả | Artifact SHA-256 |
|---|---|---|---|
| V2 `fe57f7d` | Preflight `17/17`; provider smoke `5/5` | Chạy đủ `17/17`, cost ratio `1.349857` đạt nhưng latency p95 ratio `2.226645 > 1.25`; retrieval ratio `2.083333`. Graph traversal của tail case chỉ `333 ms`. | Outcome `e71541fff80b06b1d82faa9924a9648b9a95f00967161f467585c98b245a0519` |
| V3 window-01 `32fc8d7` | Preflight `17/17`; smoke evaluation-only `5/5`, `0` retry | Dừng ở warm-up pair 1. Baseline sạch; candidate strict BM25 mất `3236 ms`, trả `ResponseHandlingException`, sau đó broad BM25 pass `227 ms`. Không có measured pair. | Declaration `7aff6ed38551429ba67af5c63ce0f0606d2b9fcc0a0a391c577edf0a786c18f3`; outcome `7a2629ce0652cabe2571a3d5094f57e620d1346bf26177fbe7cce3cbac14d774` |
| Exact sparse recovery probe | Cùng query và strict RBAC/publication filter, timeout `3 s` | Pass `5/5`: lượt đầu `850.549 ms`, bốn lượt sau `234.681–239.918 ms`. Điều này loại lỗi request/filter deterministic nhưng không chứng minh data plane ổn định suốt window. | `1a727262f6fdfcd35d1f713e2e316321f2ae76ae539b9b61be47292b27ed9e2d` |
| V3 window-02 `32fc8d7` | Preflight `17/17`; smoke mới `5/5`, `0` retry; warm-up mirrored `2/2` sạch | Case 1 sạch về health/provider/fallback: baseline/candidate `10624.14/9509.51 ms`. Case 2 baseline strict explicit retrieval trả `ResponseHandlingException`, chuyển `hybrid_fallback`; retrieval `8615 ms`, total `18759.45 ms`. Candidate case 2 không chạy; window dừng ở `2/17`. | Declaration `726d1f40c73f9de5e84788f2b0fdb81b799fdebb3b08d020b2e231625d0df8a5`; outcome `f26cf9c60c81202ff65945e1e9b4210baae5772f9b3f3ab6ec1201bd78d8f831` |

V3 dùng hai warm-up pair cố định trên `graph-uses-material`: `baseline-first`, rồi `candidate-first`. Warm-up có trace riêng, không đi vào measured latency/cost/quality và dừng trước measured phase khi có health, stage, provider, fallback, retry hoặc execution anomaly. Full 17-case warm-up không được dùng vì sẽ có nguy cơ vượt provider-smoke freshness tối đa `30` phút.

## Facts

- Tail retrieval xảy ra ở cả candidate và baseline, trước Graph traversal; vì vậy không thể quy riêng cho Graph traversal.
- Hai V3 window độc lập đều có `ResponseHandlingException` trong Qdrant retrieval dù provider smoke pass và Qdrant health pass.
- Window-01 có một BM25 event đồng thời mang `error` và `fallback`; aggregate `provider_failure_count=2` là cách evaluator đếm cùng một event ở hai dấu hiệu, không phải hai anomaly độc lập. Window-02 có cùng semantics cho một `hybrid_fallback` event.
- Recovery probe `5/5` chỉ chứng minh một khoảng ngắn đã hồi phục. Nó không dự báo được tail tái xuất hiện trong full pipeline vài phút sau.
- Không window V3 nào tạo đủ measured pairs để tính p95/cost/arm-order conclusion. Không được carry-forward case 1 của window-02.

## Inferences

- Bằng chứng phù hợp nhất với Qdrant search latency/availability tail không ổn định dưới evaluation workload; cold-start đơn lẻ không còn giải thích đủ vì window-02 đã qua warm-up `2/2` rồi mới lỗi ở measured case 2.
- Tăng search timeout sẽ trực tiếp làm xấu latency gate. Cho phép retry hoặc bỏ stop-on-fallback sẽ nới oracle đã khóa. Bỏ case, thay percentile hoặc chọn rerun đẹp sẽ làm mất tính hợp lệ của benchmark.
- Vì vậy bằng chứng hiện tại chưa chỉ ra root fix code-local an toàn trong current provider/data-plane/corpus/product scope.

## Unknowns

- Chưa có Qdrant server-side latency, resource saturation hoặc network telemetry để phân biệt host contention, index/cache behavior và transport tail.
- `ResponseHandlingException` được artifact hóa theo error type để giữ secret-safe; báo cáo không có provider-side request diagnostics.
- Chưa biết một Qdrant deployment/capacity profile khác có giữ được zero-fallback và latency gate trên cùng 17 case hay không.

## Khuyến nghị disposition

Đề nghị `keep_off_technical_limit` cho Graph Retrieval trong current scope:

- Graph Retrieval, formal series, controlled-demo pilot `single_owner` 20 case và default rollout tiếp tục OFF.
- Không chạy thêm same-design window, không tăng timeout/retry, không cho phép fallback, không đổi percentile và không bỏ case.
- Chỉ mở lại sau một thay đổi ngoài current scope có bằng chứng, ví dụ Qdrant capacity/deployment/telemetry được thay đổi và owner phê duyệt một declaration mới. Mọi ngưỡng quality, provenance, RBAC, safety, latency và cost giữ nguyên.
- Sau thay đổi đó phải chạy lại từ preflight, exact retrieval recovery, provider smoke, supporting diagnostic đủ `17/17`, rồi mới xét formal series mới; không reuse artifact hiện tại.

Disposition chỉ có hiệu lực sau `tran.nghi` technical review và `bao.nguyen` chấp nhận. Cho đến lúc đó đây là khuyến nghị fail-closed, không phải release decision.
