# Free reranker options for Vietnamese RAG

Date: 2026-07-29

## API-first recommendation

For this project's zero-budget hosted path, trial Jina AI
`jina-reranker-v3` first:

- The free key is currently documented at 100 RPM, 100,000 TPM, and two
  concurrent requests, with 10 million starting tokens.
- The hosted model supports multilingual retrieval, including Vietnamese in
  its published MKQA evaluation.
- This is materially more usable than the observed Voyage project limit of
  3 RPM and 10,000 TPM.

This is not yet evidence that Jina beats Voyage on this project's Vietnamese
documents. Run the same query/candidate pairs through both providers and compare
ranking quality, p95 latency, errors, and token use before changing the default.

Cohere is the fallback trial candidate, not the first choice: its free trial
rerank limit is currently 10 requests/minute and 1,000 API calls/month. Pinecone
Starter and Mixedbread credits are useful for experiments, but their free usage
is a plan or one-time credit rather than a durable no-cost runtime contract.

Jina is close to the current request/response shape but not a drop-in endpoint
swap. Its request uses `top_n`; the Voyage adapter currently sends `top_k`.
Add a small provider-specific adapter rather than reusing
`voyage_rerank_documents()` with a changed URL.

Primary sources:

- Jina Reranker API, free quota, limits, languages, and model details:
  https://jina.ai/en-US/reranker/
- Jina Reranker v3 evaluation:
  https://jina.ai/news/jina-reranker-v3-0-6b-listwise-reranker-for-sota-multilingual-retrieval/
- Cohere trial rate limits:
  https://docs.cohere.com/v2/docs/rate-limits
- Pinecone Starter rerank availability and limits:
  https://docs.pinecone.io/assistant-release-notes/2024 and
  https://docs.pinecone.io/reference/api/database-limits
- Mixedbread one-time Starter credits:
  https://www.mixedbread.com/pricing

## Conclusion

Keep the current deterministic `local_fusion` path as the default for this Windows box: i5-11400, 32GB RAM, Intel UHD 730, no CUDA. It is already implemented, has no external dependency, and is the documented fallback when Voyage fails.

If a zero-cost neural reranker is worth testing, use
`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` as the first local experiment. It
is Apache-2.0, about 118M parameters, and its mMARCO training data explicitly
includes Vietnamese. It also fits the installed `sentence-transformers`/`torch`
stack. Keep it opt-in and cap candidates at the existing 20-document rerank
budget.

Use `Alibaba-NLP/gte-multilingual-reranker-base` as the second experiment only
if MiniLM quality is insufficient. GTE is multilingual and Apache-2.0, but at
about 306M parameters it is a materially heavier CPU inference path.

Do not make `BAAI/bge-reranker-v2-m3` the CPU default. It is the strongest local open model on paper, but a 568M cross-encoder on CPU will add the most latency and cold-start cost. Use it only for offline A/B evidence or a controlled demo.

Do not use `cross-encoder/ms-marco-MiniLM-L6-v2` as the Vietnamese default. It is attractive because it is small, but the official model card positions it for MS MARCO passage ranking, not multilingual/Vietnamese retrieval.

Free APIs are backup candidates, not a better default:

- Jina AI is the only realistic free API backup to trial because the API has a free allowance and reranker product support. Use it only where external processing is permitted.
- Cohere trial/free keys are PoC-grade, not a production-free default.
- Hugging Face Inference is not a stable free rerank backend for this app; free monthly credits are small and provider availability/pricing are not a deterministic runtime contract.

## Project baseline

The current project already has the boring working path:

- `RerankPolicy` returns `local_fusion` when external processing is not allowed, Voyage is disabled, or no API key is resolved. Source: `src/mech_chatbot/rag/rerank.py:40-64`.
- Voyage failure metadata records immediate fallback to `local_fusion` with no retry. Source: `src/mech_chatbot/rag/rerank.py:20-31`.
- Voyage calls are capped by runtime top-n limits; current code uses `rerank_top_n_cap`, default 20. Source: `src/mech_chatbot/rag/phases/retrieval_rerank.py:125-159`.
- Candidate diversification already limits duplicate sections/documents before rerank. Source: `src/mech_chatbot/rag/rerank.py:211-263`.
- The documented retrieval architecture is dense + BM25 + RRF, mode `explicit_dense_bm25_rrf`. Source: `docs/retrieval-architecture.md:5-10`.
- ADR 0002 says Voyage 429 or any provider error falls back locally and aborts a pilot window if rerank errors exceed 5% over 50 completed calls. Source: `docs/adr/0002-crag-pilot-isolation-and-voyage-fallback.md:23-28`.
- `sentence-transformers`, `torch`, and `transformers` are already installed in the locked environment. Source: `requirements.lock.txt:83-95`.

## Local open-source options

| Option | Fit | License / commercial use | Vietnamese fit | CPU fit on i5-11400 | Recommendation |
| --- | --- | --- | --- | --- | --- |
| Current `local_fusion` | Already shipped | Internal code | Good enough baseline because it uses existing dense + BM25 + RRF and Vietnamese tokenization helpers | Best | Keep default |
| `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Multilingual cross-encoder, about 118M parameters | Apache-2.0 on Hugging Face model card | Its mMARCO training data includes Vietnamese | Best practical neural CPU fit | First local neural experiment |
| `Alibaba-NLP/gte-multilingual-reranker-base` | Multilingual reranker, 306M class | Apache-2.0 on Hugging Face model card | Strong multilingual coverage | Plausible with cap 20, still benchmark p95 | Second experiment if MiniLM quality is insufficient |
| `BAAI/bge-reranker-v2-m3` | Multilingual BGE reranker, 568M class | Apache-2.0 on Hugging Face model card | Strong | Worst latency/cold start on CPU | Offline A/B only, not default |
| Jina local rerankers | Strong multilingual docs and API | Many local Jina reranker weights are non-commercial on model cards; verify exact card before self-hosting | Good | Depends on model | Prefer Jina API over self-host unless license is explicitly OK |

Primary sources:

- BGE reranker docs/model: https://bge-model.com/tutorial/5_Reranking/5.2.html and https://huggingface.co/BAAI/bge-reranker-v2-m3
- Multilingual MiniLM model card and training dataset:
  https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 and
  https://huggingface.co/datasets/unicamp-dl/mmarco
- GTE model card: https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base
- Jina reranker model card/API docs: https://huggingface.co/jinaai/jina-reranker-v3 and https://jina.ai/reranker/

## Free API options

| API | Free tier reality | Commercial/runtime concern | Project fit |
| --- | --- | --- | --- |
| Jina AI Reranker | Official Jina pages advertise free starting credits/allowance and hosted reranker endpoints | External processing must be allowed for each document; provider limits can change | Best API backup to trial, but not default |
| Cohere Rerank | Trial/free access exists with low rate limits | Trial limits and quota make it PoC-grade | Not primary |
| Hugging Face Inference Providers | Official pricing has small free monthly credits for HF accounts | Provider routing, quota, and cost can vary | Not a stable production-free reranker |
| Voyage | Already integrated; prior docs had a large free token allowance | Current repo has observed 429 fallback policy and fail-closed gates | Keep as configured path if key exists; local fallback remains mandatory |

Primary sources:

- Jina reranker/API: https://jina.ai/reranker/ and https://jina.ai/embeddings/
- Cohere rate limits: https://docs.cohere.com/docs/rate-limits
- Hugging Face pricing/inference providers: https://huggingface.co/pricing and https://huggingface.co/docs/inference-providers/index
- Voyage pricing/rerank docs: https://docs.voyageai.com/docs/pricing and https://docs.voyageai.com/docs/reranker

## Minimal next step

Do one local A/B script before any config change:

1. Keep current `local_fusion` as arm A.
2. Add a temporary, non-default runner using `sentence-transformers`
   `CrossEncoder` for `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`.
3. Reuse the existing cap of 20 candidates.
4. Measure Vietnamese Recall/NDCG and p95 latency on the same query set.

Switch only if MiniLM improves nDCG/Recall on real Vietnamese project queries
without breaking p95 latency. Test GTE only when MiniLM quality is insufficient;
otherwise keep the current path.

Skipped: new provider code and dependency changes. Add them only after the one-file A/B check proves GTE beats `local_fusion` on this repo.
