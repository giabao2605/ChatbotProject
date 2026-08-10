# decomposition-eval-v1

Manifest dùng fixture staging `crag-eval-v1`; DocID và SourceID được preflight giải quyết lúc chạy.

- `eval_manifest.jsonl`: Query-only, 13 case gồm 10 complex và 3 simple; Grounded Math phải OFF. Các case high-risk terminal không kỳ vọng claim hoặc citation được render.
- `math_query_interaction_manifest.jsonl`: 3 case Math+Query giữ nguyên expectation `full_answer` và phép `sum`; không dùng làm formal evidence cho Query-only.

Query-only SHA-256: `0746640678eeb8f9b17b0a21c302d3a5d854ccab8f460a106711b57826dee64a`

Math+Query interaction SHA-256: `258d7fdac3f41e8ab599a2ed8e0a35384a5eea6d5e0b21b2019a65c1b698440c`
