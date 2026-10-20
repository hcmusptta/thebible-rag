import logging
import os
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
import torch
from dotenv import load_dotenv
from langchain_core.embeddings import Embeddings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
_LOAD_LOCK = threading.Lock()


def model_key(model_name: str) -> str:
    path = Path(model_name)
    return os.path.normcase(str(path.resolve())) if path.is_dir() else model_name


class TextEmbedder(Embeddings):
    def __init__(self, model_name: str | None = None, device: str = "auto") -> None:
        model_name = model_name or os.getenv(
            "EMBEDDING_MODEL_NAME",
            "AITeamVN/Vietnamese_Embedding_v2",
        )
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._encode_lock = threading.Lock()
        logger.info("Creating embedding model on %s", device)
        # RAGAS expects .model to be a name when recording embedding usage.
        self.model = model_name
        self._encoder = SentenceTransformer(model_name, device=device)
        vector = self._encoder.encode("Checking", convert_to_numpy=True)
        if vector.size == 0 or not np.isfinite(vector).all():
            raise RuntimeError("Embedding model cannot create vector")
        logger.info("Loaded embedding model %s, dimension=%s", model_name, len(vector))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._encode_lock:
            embeddings = self._encoder.encode(
                texts,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        return embeddings.tolist()

    def embed_query(self, query: str) -> list[float]:
        return self.embed_documents([query])[0]


@lru_cache(maxsize=2)
def _load_embedder(model_name: str, device: str) -> TextEmbedder:
    return TextEmbedder(model_name, device=device)


def get_embedder(model_name: str, device: str = "auto") -> TextEmbedder:
    with _LOAD_LOCK:
        return _load_embedder(model_key(model_name), device)


def get_default_embedder() -> TextEmbedder:
    return get_embedder(
        os.getenv("EMBEDDING_MODEL_NAME") or "AITeamVN/Vietnamese_Embedding_v2",
        os.getenv("EMBEDDING_DEVICE", "auto"),
    )
