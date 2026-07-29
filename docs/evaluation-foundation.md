# Evaluation foundation và rollout guardrails

Tài liệu này là runbook cho mục 2.1 và 2.2 của [`doichieukientruc-progress-roadmap.md`](doichieukientruc-progress-roadmap.md).

## Rollout guardrails

`rollout-evidence-pair-v1` là contract đầu vào chung cho một cặp baseline/candidate. Mỗi pair phải ghi:

- `run_id`, `stage` và `evidence_type` là `staging_evaluation` hoặc `production_pilot`.
- Baseline/candidate context gồm commit, manifest SHA-256, snapshot fingerprint, provider configuration SHA-256, concurrency, governance scope SHA-256 và collection.
- Data-plane mutation mode là `staging`, `shadow` hoặc `read_only`; collection production chỉ được `read_only`.
- Gate có `wrong_answer_not_increased` và `leakage_zero`.
- Rollback khai báo feature flags, mặc định tắt và bằng chứng rollback test.

Kiểm tra một series trước khi cân nhắc production:

```powershell
.\chat_env\Scripts\python.exe -m scripts.eval.rollout_guardrails `
  --stage crag `
  --pair reports\run-1\rollout_pair.json `
  --pair reports\run-2\rollout_pair.json `
  --pair reports\run-3\rollout_pair.json `
  --decisions reports\milestone-decisions.json `
  --output reports\crag-series-guardrail.json
```

Guardrail mặc định yêu cầu ít nhất ba run ID độc lập, điều kiện benchmark giống nhau và decision artifact của milestone phụ thuộc. Unit-test result không phải rollout evidence.

CRAG rollout runner ghi `rollout_pair.json` tự động. Guardrail mở lại các file eval, trace, gate và rollback từ đường dẫn trong pair, tính lại SHA-256 và kiểm tra schema; giá trị hash hoặc boolean tự khai báo không được dùng làm bằng chứng. Gate phải ghi hash của đúng bốn input baseline/candidate eval/trace, còn eval/trace phải khớp commit, manifest, snapshot, provider, governance, collection, concurrency và cửa sổ trace đã khai báo trong pair. Pair chỉ có `production_eligible=true` khi rollback evidence có schema `rollback-test-evidence-v1`, `passed=true`, trùng commit và chứa đúng hai flag `RAG_CRAG_ENABLED`, `RAG_CLAIM_REPAIR_ENABLED`. Decision ledger khởi đầu cho evaluation foundation nằm tại [`examples/milestone-decisions.json`](examples/milestone-decisions.json).

## Manifest evaluation v2

Manifest mới dùng `manifest_schema: rag-eval-manifest-v2`. Answer outcome phải có ground truth do người đánh giá cung cấp, độc lập với generation model:

```json
{
  "manifest_schema": "rag-eval-manifest-v2",
  "expected_claims": [
    {
      "id": "nominal-value",
      "required_terms": ["1,500"],
      "allowed_source_ids": ["D41P1"]
    }
  ],
  "expected_citations": [
    {
      "document": "numbers.md",
      "doc_id": 41,
      "page": 1,
      "version": 12,
      "source_id": "D41P1"
    }
  ]
}
```

Manifest không có `manifest_schema` được đọc dưới tên `rag-eval-manifest-v1-legacy`. Legacy case không được dùng để kết luận claim precision hoặc citation accuracy nếu chưa có label tương ứng.

## Artifact evaluator v4

`run_eval.py` ghi schema `rag-labeled-eval-v4` với:

- Recall@5/10/20, nDCG@5/10 và MRR.
- Rank list và expected source identity của từng case.
- Claim precision, expected-claim recall và faithfulness.
- Citation accuracy/precision cùng violation cho missing citation, wrong page/version/source và inaccessible source.
- Risk–coverage tại các operating point cố định; evaluator không tự chọn threshold.
- Evaluator version, deterministic model identifiers và manifest schema version.

Các tỷ lệ claim/citation ở cấp report là micro-average có `numerator`, `denominator` và `value`; không lấy trung bình các tỷ lệ case có kích thước khác nhau.

## Adjudication protocol

Mỗi case cần hai reviewer độc lập. Nếu outcome, answer correctness hoặc citation correctness khác nhau, phải có reviewer thứ ba với role `adjudicator`. Mọi label phải có `reason_code`; artifact không ghi raw prompt.

Tái tạo worked example:

```powershell
.\chat_env\Scripts\python.exe -m scripts.eval.adjudicate `
  data\evaluation_foundation_v1\adjudication_worked_example.jsonl `
  --json-output docs\examples\evaluation-adjudication-v1.json `
  --markdown-output docs\examples\evaluation-adjudication-v1.md
```

Hai file output phải có nội dung giống nhau khi chạy lại trên cùng input và commit.
