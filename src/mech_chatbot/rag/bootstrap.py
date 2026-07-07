# -*- coding: utf-8 -*-
"""Auto-split tu rag/service.py (P1.2 refactor). Giu nguyen logic goc; chi tach file + import."""

import os
import warnings
from mech_chatbot.config.settings import QDRANT_COLLECTION
from dotenv import load_dotenv
from mech_chatbot.config.logging import logger, log_trace
from qdrant_client import QdrantClient, models
from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from langchain_huggingface import HuggingFaceEmbeddings
from mech_chatbot.llm.llm_client import cohere_invoke, get_cohere_llm, _is_cohere_rate_limit, gpt_rerank_documents, get_llm_model_name
import threading
from mech_chatbot.llm.vision_client import build_vision_model, is_retryable_error


os.environ["TRANSFORMERS_VERBOSITY"] = "error"


warnings.filterwarnings("ignore", category=FutureWarning)


load_dotenv()


logger.info("Dang khoi dong he thong RAG AI...")


_VISION_MODEL = build_vision_model()


def env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


STRICT_ANSWER_MODE = env_bool("STRICT_ANSWER_MODE", True)


RERANK_PER_PART = int(os.getenv("RERANK_PER_PART", "10"))


RERANK_TOP_N_CAP = int(os.getenv("RERANK_TOP_N_CAP", "40"))


def use_gpt_rerank():
    return str(os.getenv("USE_GPT_RERANK", "true")).strip().lower() in {"1", "true", "yes", "y", "on"}


class RAGSystem:
    _instance = None
    _lock = threading.Lock()
 
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls._init_components()
        return cls._instance
 
    @staticmethod
    def _init_components():
        # Ket noi Qdrant Cloud
        qdrant_url = os.getenv("QDRANT_URL", "")
        qdrant_api_key = os.getenv("QDRANT_API_KEY", "")
        
        if not qdrant_url or not qdrant_api_key:
            raise ValueError("Thieu thiet lap QDRANT_URL hoac QDRANT_API_KEY trong file .env")
            
        logger.info(f"   -> Ket noi Qdrant Cloud tai: {qdrant_url}")
        client = QdrantClient(
            url=qdrant_url,
            api_key=qdrant_api_key,
            timeout=120,
        )
 
        embed_model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
        embed_device = os.getenv("EMBEDDING_DEVICE", "cpu").strip() or "cpu"
        logger.info(f"   -> Dang tai model Embedding: {embed_model} tren {embed_device}")

        embeddings = HuggingFaceEmbeddings(
            model_name=embed_model,
            model_kwargs={"device": embed_device},
            encode_kwargs={"normalize_embeddings": True}
        )
 
        logger.info("   -> Dang khoi tao mo hinh BM25 (Qdrant/bm25)...")
        sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")
 
        if not client.collection_exists(QDRANT_COLLECTION):
            logger.info(f"   -> Collection '{QDRANT_COLLECTION}' khong ton tai. Dang tao moi...")
            embedding_dim = int(os.getenv("EMBEDDING_DIM", "1024"))
            client.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=models.VectorParams(
                    size=embedding_dim,
                    distance=models.Distance.COSINE
                ),
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams(
                        index=models.SparseIndexParams(
                            on_disk=False,
                        )
                    )
                }
            )

        # NOTE: Payload indexes are managed by scripts/create_qdrant_indexes.py
        # Run that script once during initial setup or after schema changes.
        # Removed from here to speed up cold-start time.
 
        vectorstore = QdrantVectorStore(
            client=client,
            collection_name=QDRANT_COLLECTION,
            embedding=embeddings,
            sparse_embedding=sparse_embeddings,
            sparse_vector_name="sparse",
            retrieval_mode=RetrievalMode.HYBRID
        )
 
        logger.info(f"   -> Dang ket noi GPT model: {get_llm_model_name()}...")
        llm = get_cohere_llm()
 
        return client, vectorstore, llm


client, vectorstore, llm = RAGSystem.get_instance()


RERANK_SCORE_CUTOFF = float(os.getenv("RERANK_SCORE_CUTOFF", "0.3"))

__all__ = [
    '_VISION_MODEL',
    'env_bool',
    'use_gpt_rerank',
    'STRICT_ANSWER_MODE',
    'RERANK_PER_PART',
    'RERANK_TOP_N_CAP',
    'RAGSystem',
    'client',
    'vectorstore',
    'llm',
    'RERANK_SCORE_CUTOFF',
]
