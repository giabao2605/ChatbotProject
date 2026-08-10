# decomposition-eval-v1

Manifest dùng fixture staging `crag-eval-v1`; DocID và SourceID được preflight giải quyết lúc chạy.

- `eval_manifest.jsonl`: Query-only, 13 case gồm 10 complex và 3 simple; Grounded Math phải OFF. Các case high-risk terminal khóa claim và citation render bằng `0`.
- `math_query_interaction_manifest.jsonl`: 3 case Math+Query giữ nguyên expectation `full_answer` và phép `sum`; không dùng làm formal evidence cho Query-only.

Citation truy xuất được so khớp theo tập canonical source identity duy nhất: nhiều chunk cùng một nguồn/trang được gộp, nhưng bất kỳ source identity khác expectation đều làm gate fail.

Query-only SHA-256: `6976cbbe4c9500b7c0755c5944775e326106a780bb2910bfa71167787a1d0bf8`

Math+Query interaction SHA-256: `d21495e86faca22c455f745a7b9fd7f249643f31e76f36e086f8af4467b0932b`
