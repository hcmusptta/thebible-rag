import json
import os
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


load_dotenv()

_PROVIDER_NAMES = {
    "ollama": "Ollama",
    "google": "Google",
    "hf": "Hugging Face",
}


def _provider_model(provider: str) -> str:
    models = {
        "ollama": os.getenv("OLLAMA_MODEL", "qwen3:8b"),
        "google": os.getenv("GOOGLE_MODEL", "gemini-flash-latest"),
        "hf": os.getenv("HF_MODEL", ""),
    }
    return models.get(provider, "")


def _provider_is_configured(provider: str) -> bool:
    if provider == "ollama":
        return True
    if provider == "google":
        return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("API_KEY"))
    if provider == "hf":
        return bool(os.getenv("HF_MODEL"))
    return False


def get_public_runtime_config() -> dict[str, Any]:
    requested = [
        item.strip().lower()
        for item in os.getenv("LLM_PROVIDERS", "ollama").split(",")
        if item.strip()
    ]
    providers = [
        {"name": _PROVIDER_NAMES[provider], "model": _provider_model(provider)}
        for provider in requested
        if provider in _PROVIDER_NAMES and _provider_is_configured(provider)
    ]
    return {
        "runtime": {
            "application": "RAG Chatbot",
            "framework": f"Chainlit {version('chainlit')}",
            "python": sys.version.split()[0],
        },
        "llm": {
            "providers": providers,
            "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            "temperature": os.getenv("OLLAMA_TEMPERATURE", "0"),
            "ollama_reasoning": os.getenv("OLLAMA_REASONING", "false"),
            "timeout_seconds": os.getenv("LLM_TIMEOUT", "120"),
            "ollama_num_ctx": os.getenv("OLLAMA_NUM_CTX", "16384"),
            "max_output_tokens": os.getenv("RAG_MAX_OUTPUT_TOKENS", "2048"),
        },
        "retrieval": {
            "embedding_model": os.getenv(
                "EMBEDDING_MODEL_NAME", "AITeamVN/Vietnamese_Embedding_v2"
            ),
            "reranker_model": os.getenv(
                "RERANKER_MODEL_NAME", "AITeamVN/Vietnamese_Reranker"
            ),
            "reranker_enabled": os.getenv("RERANK_ENABLED", "true").lower() in {"1", "true", "yes"},
            "embedding_device": os.getenv("EMBEDDING_DEVICE", "auto"),
            "reranker_device": os.getenv("RERANKER_DEVICE", "auto"),
            "max_context_chars": os.getenv("RAG_CONTEXT_MAX_CHARS", "40000"),
            "context_mode": "source_paragraphs_and_reviewed_passages",
            "semantic_queries": "original, resolved standalone question, at most two subject-preserving probes; same source filters",
            "scripture_selection": "full matched verses selected in bounded batches; canonical headings in BM25",
            "scripture_answer": "source quotes for explicit titles and short lists with fixed proof IDs; generated claims independently checked; one bounded repair",
            "semantic_context_budget": "drop lower priority whole paragraphs or optional exact context; keep requested references intact",
            "rrf_k": os.getenv("RAG_RRF_K", "60"),
            "filter_fallback": False,
            "scripture_processing_version": 4,
            "canonical_store": "SQLite snapshots bound to committed vector runs",
            "exact_references": "all requested references; no semantic top-k",
            "exhaustive_search": "literal phrase over selected corpus",
            "top_k": os.getenv("RAG_TOP_K", "4"),
            "candidate_limit": os.getenv("RAG_CANDIDATE_LIMIT", "20"),
        },
        "storage": {
            "database_path": os.getenv("CHROMA_DB_PATH", "chroma_db"),
            "collection": os.getenv(
                "CHROMA_COLLECTION", "rag_vietnamese_embedding_v2_v1"
            ),
        },
        "documents": {
            "supported_formats": ['pdf','docx','doc','txt'],
            "max_document_size_mb": os.getenv("MAX_DOCUMENT_SIZE_MB") or os.getenv("MAX_PDF_SIZE_MB", "100"),
            "max_pdf_size_mb": os.getenv("MAX_PDF_SIZE_MB", "100"),
            "max_pdf_pages": os.getenv("MAX_PDF_PAGES", "3500"),
            "embedding_batch_size": os.getenv("EMBEDDING_BATCH_SIZE", "50"),
            "summary_max_chars": os.getenv("SUMMARY_MAX_CHARS", "120000"),
            "summary_chunk_chars": os.getenv("SUMMARY_CHUNK_CHARS", "12000"),
        },
        "benchmark": {
            "answer_provider": os.getenv("LLM_PROVIDERS", "ollama"),
            "judge_provider": "ollama",
            "judge_model": os.getenv("RAGAS_JUDGE_MODEL", "qwen3.5:9b"),
            "judge_temperature": os.getenv("RAGAS_JUDGE_TEMPERATURE", "0"),
            "judge_num_ctx": os.getenv("RAGAS_JUDGE_NUM_CTX", "8192"),
            "judge_max_tokens": os.getenv("RAGAS_JUDGE_MAX_TOKENS", "2048"),
            "judge_reasoning": False,
            "sample_size": os.getenv("RAGAS_SAMPLE_SIZE", "25"),
            "embedding_model": os.getenv("RAGAS_EMBEDDING_MODEL")
            or os.getenv("EMBEDDING_MODEL_NAME", "AITeamVN/Vietnamese_Embedding_v2"),
            "top_k": os.getenv("RAGAS_TOP_K", os.getenv("RAG_TOP_K", "4")),
            "candidate_limit": os.getenv("RAGAS_CANDIDATE_LIMIT", "40"),
            "timeout_seconds": os.getenv("RAGAS_TIMEOUT", "600"),
        },
    }


def write_public_runtime_config(target: Path) -> None:
    content = json.dumps(get_public_runtime_config(), ensure_ascii=False, indent=2) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return
    target.write_text(content, encoding="utf-8")
