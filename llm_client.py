import os
from langchain_cohere import ChatCohere
from tenacity import retry, retry_if_exception, wait_exponential, stop_after_attempt
from dotenv import load_dotenv

load_dotenv()

# Khoi tao LLM
llm = ChatCohere(
    model=os.getenv("COHERE_MODEL_NAME", "command-r-08-2024"),
    temperature=0,
    max_tokens=4000,
    cohere_api_key=os.getenv("COHERE_API_KEY")
)

def _is_cohere_rate_limit(exc):
    msg = str(exc).lower()
    return "429" in msg or "too many requests" in msg or "rate limit" in msg

@retry(
    retry=retry_if_exception(_is_cohere_rate_limit),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(4),
)
def cohere_invoke(messages):
    return llm.invoke(messages)

def get_cohere_llm():
    return llm
