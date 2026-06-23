import os
from tenacity import retry, retry_if_exception, wait_exponential, stop_after_attempt
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

load_dotenv()


def _get_api_key():
    """ProxyLLM dùng API kiểu OpenAI-compatible."""
    return (
        os.getenv("PROXYLLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("GPT_API_KEY")
    )


def _get_base_url():
    return (
        os.getenv("PROXYLLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "https://api.proxyllm.eu/v1"
    )


def get_llm_model_name():
    return os.getenv("GPT_MODEL_NAME", "gpt-5.4")


def _make_llm(max_tokens=None):
    api_key = _get_api_key()
    if not api_key:
        raise ValueError("Thieu PROXYLLM_API_KEY hoac OPENAI_API_KEY trong file .env")

    return ChatOpenAI(
        model=get_llm_model_name(),
        api_key=api_key,
        base_url=_get_base_url(),
        temperature=float(os.getenv("GPT_TEMPERATURE", "0")),
        max_tokens=max_tokens or int(os.getenv("GPT_MAX_OUTPUT_TOKENS", "4000")),
        timeout=float(os.getenv("GPT_TIMEOUT_SECONDS", "120")),
        max_retries=0,  # retry do tenacity ben duoi xu ly de log ro hon
    )


# Khoi tao LLM chinh thay cho Cohere Command R
llm = _make_llm()


def _is_gpt_rate_limit(exc):
    msg = str(exc).lower()
    return (
        "429" in msg
        or "too many requests" in msg
        or "rate limit" in msg
        or "resource_exhausted" in msg
        or "temporarily unavailable" in msg
        or "timeout" in msg
    )


# Giu ten cu de cac file khac khong phai sua nhieu.
def _is_cohere_rate_limit(exc):
    return _is_gpt_rate_limit(exc)


@retry(
    retry=retry_if_exception(_is_gpt_rate_limit),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(4),
)
def gpt_invoke(messages):
    return llm.invoke(messages)


# Alias tuong thich nguoc: code cu goi cohere_invoke -> nay thuc chat goi GPT-5.4
def cohere_invoke(messages):
    return gpt_invoke(messages)


def get_gpt_llm():
    return llm


# Alias tuong thich nguoc: code cu goi get_cohere_llm -> nay tra GPT-5.4 llm
def get_cohere_llm():
    return llm


def _doc_text_for_rerank(doc, idx):
    meta = getattr(doc, "metadata", {}) or {}
    source = f"file={meta.get('file_goc')}, page={meta.get('trang_so')}, type={meta.get('loai_du_lieu')}"
    text = meta.get("noi_dung_goc") or getattr(doc, "page_content", "") or ""
    text = str(text)
    if len(text) > 1800:
        text = text[:1800] + "..."
    return f"[{idx}] {source}\n{text}"


def _safe_json_array(text):
    import json, re
    raw = str(text or "").strip().replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(raw)
    except Exception:
        m = re.search(r"\[[\s\S]*\]", raw)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except Exception:
            return []
    return data if isinstance(data, list) else []


@retry(
    retry=retry_if_exception(_is_gpt_rate_limit),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(3),
)
def gpt_rerank_documents(documents, query, top_n=10):
    """
    Rerank nhe bang GPT-5.4 thay Cohere Rerank.
    Tra ve list Document, gan metadata['relevance_score'] tu 0..1.
    """
    docs = list(documents or [])
    if not docs:
        return []

    # De tiet kiem token, chi dua toi da 30 docs vao GPT rerank.
    max_docs = int(os.getenv("GPT_RERANK_MAX_DOCS", "30"))
    candidate_docs = docs[:max_docs]
    top_n = max(1, min(int(top_n or 10), len(candidate_docs)))

    joined = "\n\n---\n\n".join(_doc_text_for_rerank(d, i) for i, d in enumerate(candidate_docs))
    prompt = f"""
Bạn là bộ rerank cho hệ thống RAG tài liệu cơ khí.
Nhiệm vụ: chọn các đoạn tài liệu liên quan nhất để trả lời câu hỏi.

Câu hỏi:
{query}

Danh sách tài liệu ứng viên:
{joined}

Hãy trả về DUY NHẤT một JSON array. Mỗi phần tử là object có 2 key: index và score.
Ví dụ JSON hợp lệ: một mảng các object, mỗi object có index=0 và score=0.95

Quy tắc:
- Chỉ dùng index có trong danh sách.
- score từ 0 đến 1.
- Sắp xếp giảm dần theo độ liên quan.
- Trả tối đa {top_n} phần tử.
- Không thêm giải thích ngoài JSON.
"""
    resp = llm.invoke([HumanMessage(content=prompt)])
    arr = _safe_json_array(resp.content)

    ranked = []
    seen = set()
    for item in arr:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except Exception:
            continue
        if idx < 0 or idx >= len(candidate_docs) or idx in seen:
            continue
        seen.add(idx)
        score = item.get("score", 0.5)
        try:
            score = float(score)
        except Exception:
            score = 0.5
        score = max(0.0, min(1.0, score))
        d = candidate_docs[idx]
        d.metadata["relevance_score"] = score
        ranked.append(d)
        if len(ranked) >= top_n:
            break

    # Fallback an toan neu GPT tra JSON loi
    if not ranked:
        for d in candidate_docs[:top_n]:
            d.metadata["relevance_score"] = 1.0
        return candidate_docs[:top_n]

    return ranked
