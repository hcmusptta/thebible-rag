import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager, closing
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from dotenv import load_dotenv
from filelock import FileLock, Timeout

from services.query_normalization import build_name_aliases, normalize_query_names


load_dotenv()

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    BM25Okapi = None


logger = logging.getLogger(__name__)
APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EMBEDDING_MODEL = "AITeamVN/Vietnamese_Embedding_v2"
INGEST_SCHEMA_VERSION = 5
_IDENTITY_KEYS = (
    "embedding_model",
    "embedding_dimension",
    "ingest_schema_version",
)


class CollectionUnavailableError(RuntimeError):
    """The collection is busy or has not safely completed ingestion."""


def _with_access(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self.access():
            return method(self, *args, **kwargs)
    return locked


def _resolve_db_path(db_path: Optional[str] = None) -> Path:
    configured = Path(db_path or os.getenv("CHROMA_DB_PATH", "chroma_db")).expanduser()
    if not configured.is_absolute():
        configured = APP_ROOT / configured
    return configured.resolve()


def _merge_overlapping_text(parts: List[str], max_overlap: int = 500) -> str:
    merged = ""
    for raw_part in parts:
        part = str(raw_part or "").strip()
        if not part:
            continue
        if not merged:
            merged = part
            continue

        overlap_limit = min(max_overlap, len(merged), len(part))
        overlap = 0
        for size in range(overlap_limit, 19, -1):
            if merged.endswith(part[:size]):
                overlap = size
                break
        merged = f"{merged}{' ' if overlap == 0 else ''}{part[overlap:]}"
    return merged


class VectorDatabase:
    def __init__(
        self,
        collection_name: str = "rag_vietnamese_embedding_v2_v2",
        db_path: Optional[str] = None,
    ):
        self.db_path = _resolve_db_path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.db_path))
        self.collection_name = collection_name
        self.embedding_model = (
            os.getenv("EMBEDDING_MODEL_NAME") or DEFAULT_EMBEDDING_MODEL
        ).strip()
        self.embedding_dimension = max(
            1, int(os.getenv("EMBEDDING_DIMENSION", "1024"))
        )
        self._lock = threading.RLock()
        lock_name = hashlib.sha256(collection_name.encode()).hexdigest()[:24]
        self._file_lock = FileLock(str(self.db_path / f".{lock_name}.lock"))
        self._access_depth = 0
        self._indexed_revision = None
        self._documents: List[Dict[str, Any]] = []
        self._bm25 = None
        self._bm25_tokens: List[List[str]] = []
        with self.access(refresh=False):
            self.collection = self._get_or_create_collection()
            self.recover_pending_ingest()
            self._rebuild_bm25()

    @contextmanager
    def access(self, *, refresh=True):
        # ponytail: one collection operation at a time; use a DB server for multi-user throughput.
        if not self._lock.acquire(blocking=False):
            raise CollectionUnavailableError("Kho dữ liệu đang bận xử lý. Vui lòng thử lại sau.")
        try:
            try:
                self._file_lock.acquire(timeout=0)
            except Timeout as error:
                raise CollectionUnavailableError(
                    "Kho dữ liệu đang được tiến trình khác sử dụng. Vui lòng thử lại sau."
                ) from error
            try:
                if self._access_depth == 0 and refresh:
                    self.collection = self.client.get_collection(self.collection_name)
                self._access_depth += 1
                try:
                    yield
                finally:
                    self._access_depth -= 1
            finally:
                self._file_lock.release()
        finally:
            self._lock.release()

    def _update_metadata(self, **changes):
        metadata = {
            key: value for key, value in (self.collection.metadata or {}).items()
            if not key.startswith("hnsw:")
        }
        for key, value in changes.items():
            if value is None:
                metadata.pop(key, None)
            else:
                metadata[key] = value
        self.collection.modify(metadata=metadata)

    def _pending_ingest(self):
        raw = (self.collection.metadata or {}).get("ingest_transaction")
        if not raw:
            return None
        transaction = json.loads(raw)
        if (
            transaction.get("phase") not in {"writing", "committing"}
            or not transaction.get("run_id")
            or not transaction.get("source")
            or not transaction.get("document_id")
        ):
            raise RuntimeError("Invalid ingest journal; collection kept unavailable.")
        return transaction

    def _run_chunks(self, transaction):
        return self._get_all_in_batches(
            include=["metadatas"], where={"ingest_run_id": transaction["run_id"]},
        )

    def _finish_ingest(self):
        self._update_metadata(
            state="ready", ingest_transaction=None, index_revision=uuid.uuid4().hex,
        )
        self._rebuild_bm25()

    @_with_access
    def begin_ingest(self, document_id: str, source: str, expected_chunks: int) -> str:
        self.ensure_collection_identity()
        self.recover_pending_ingest()
        if expected_chunks <= 0:
            raise ValueError("Ingest must contain at least one chunk.")
        transaction = {
            "run_id": uuid.uuid4().hex, "phase": "writing",
            "document_id": document_id, "source": source,
            "expected_chunks": expected_chunks,
        }
        self._update_metadata(state="building", ingest_transaction=json.dumps(transaction))
        return transaction["run_id"]

    @_with_access
    def commit_ingest(self) -> int:
        transaction = self._pending_ingest()
        if transaction is None:
            raise RuntimeError("No ingest transaction to commit.")
        chunks = self._run_chunks(transaction)
        metas = chunks.get("metadatas") or []
        expected = transaction["expected_chunks"]
        if len(chunks["ids"]) != expected or not all(
            meta.get("document_chunk_total") == expected
            and meta.get("document_id") == transaction["document_id"]
            and meta.get("source") == transaction["source"]
            and meta.get("ingest_schema_version") == INGEST_SCHEMA_VERSION
            for meta in metas
        ):
            raise RuntimeError("Ingest is incomplete; cannot publish partial vectors.")
        if any(meta.get("document_type") == "scripture" and meta.get("processing_version") == 4 for meta in metas):
            from loaddocs.scripture_store import ScriptureStore
            ScriptureStore(self).verify_run(transaction["run_id"], transaction["document_id"], expected)
        # Durable commit decision BEFORE deleting any old version. Restart rolls forward.
        transaction["phase"] = "committing"
        self._update_metadata(ingest_transaction=json.dumps(transaction))
        new_ids = set(chunks["ids"])
        old_ids = [
            chunk_id for chunk_id in self.get_chunk_ids_by_source(transaction["source"])
            if chunk_id not in new_ids
        ]
        self._delete_ids_in_batches(old_ids)
        self._finish_ingest()
        return len(old_ids)

    @_with_access
    def abort_ingest(self) -> bool:
        transaction = self._pending_ingest()
        if transaction is None or transaction["phase"] == "committing":
            # Once committing starts, recovery must finish it, not remove the new data.
            return False
        self._delete_ids_in_batches(self._run_chunks(transaction)["ids"])
        self._finish_ingest()
        return True

    @_with_access
    def recover_pending_ingest(self) -> None:
        transaction = self._pending_ingest()
        if transaction:
            logger.warning("Recovering ingest %s (%s)", transaction["run_id"], transaction["phase"])
            if transaction["phase"] == "committing":
                self.commit_ingest()
            else:
                self.abort_ingest()
            return
        metadata = self.collection.metadata or {}
        if metadata.get("state") != "building" or self.collection.count() == 0:
            return
        # Older code had no journal. Only unlock data whose completeness is provable.
        self.ensure_collection_identity()
        rows = self._get_all_in_batches(include=["metadatas"])["metadatas"]
        documents = {}
        sources = {}
        for row in rows:
            row = row or {}
            document_id = row.get("document_id")
            documents.setdefault(document_id, []).append(row)
            sources.setdefault(row.get("source"), set()).add(document_id)
        safe = all(len(ids) == 1 for ids in sources.values()) and all(
            document_id and len(metas) == metas[0].get("document_chunk_total")
            and all(
                meta.get("document_chunk_total") == len(metas)
                and meta.get("ingest_schema_version") == INGEST_SCHEMA_VERSION
                and meta.get("source")
                for meta in metas
            )
            for document_id, metas in documents.items()
        )
        if not safe:
            raise RuntimeError(
                "Collection cũ đang dở hoặc trộn phiên bản, không có nhật ký phục hồi. "
                "Dữ liệu được giữ nguyên; dùng CHROMA_COLLECTION mới và nạp lại PDF."
            )
        self._finish_ingest()

    def _get_or_create_collection(self):
        existing = self.client.list_collections()
        names = {item if isinstance(item, str) else item.name for item in existing}
        if self.collection_name in names:
            collection = self.client.get_collection(self.collection_name)
            metadata = dict(collection.metadata or {})
            self._validate_identity_values(metadata)
            missing = [key for key in _IDENTITY_KEYS if key not in metadata]
            if collection.count() == 0 and missing:
                collection.modify(
                    metadata={
                        **{
                            key: value
                            for key, value in metadata.items()
                            if not key.startswith("hnsw:")
                        },
                        "state": "building",
                        **self._identity_metadata(),
                    }
                )
            elif missing:
                logger.warning(
                    "Collection %s has no embedding identity; it is read-only "
                    "until CHROMA_COLLECTION points to a fresh collection",
                    self.collection_name,
                )
            return collection
        return self.client.create_collection(
            self.collection_name,
            metadata={
                "hnsw:space": "cosine",
                "state": "building",
                **self._identity_metadata(),
            },
        )

    def _identity_metadata(self) -> Dict[str, Any]:
        return {
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "ingest_schema_version": INGEST_SCHEMA_VERSION,
        }

    def _validate_identity_values(self, metadata: Dict[str, Any]) -> None:
        expected = self._identity_metadata()
        mismatches = [
            f"{key}={metadata[key]!r} (expected {value!r})"
            for key, value in expected.items()
            if key in metadata and metadata[key] != value
        ]
        if mismatches:
            raise RuntimeError(
                f"Collection {self.collection_name!r} is incompatible: "
                + ", ".join(mismatches)
                + ". Use a new CHROMA_COLLECTION."
            )

    def ensure_collection_identity(self) -> None:
        metadata = dict(self.collection.metadata or {})
        self._validate_identity_values(metadata)
        missing = [key for key in _IDENTITY_KEYS if key not in metadata]
        if missing:
            raise RuntimeError(
                f"Collection {self.collection_name!r} contains data without "
                f"identity metadata ({', '.join(missing)}). Use a new "
                "CHROMA_COLLECTION before ingesting."
            )

    @_with_access
    def set_collection_state(self, state: str) -> str:
        if state not in {"building", "ready"}:
            raise ValueError("Collection state must be 'building' or 'ready'.")
        self.ensure_collection_identity()
        if self._pending_ingest():
            raise RuntimeError("Use commit_ingest/abort_ingest to finish this transaction.")
        metadata = dict(self.collection.metadata or {})
        previous = str(metadata.get("state") or "building")
        metadata["state"] = state
        metadata = {
            key: value
            for key, value in metadata.items()
            if not key.startswith("hnsw:")
        }
        metadata["index_revision"] = uuid.uuid4().hex
        self.collection.modify(metadata=metadata)
        return previous

    def ensure_collection_ready(self) -> None:
        metadata = dict(self.collection.metadata or {})
        if metadata.get("state") == "building" and self.count() > 0:
            raise CollectionUnavailableError(
                "Kho dữ liệu chưa hoàn tất nạp tài liệu. "
                "Hãy đợi tác vụ hiện tại hoặc khởi động lại ứng dụng để phục hồi."
            )

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r"\w+", str(text).lower(), flags=re.UNICODE)

    @staticmethod
    def _lexical_search_text(document: Dict[str, Any]) -> str:
        metadata = document.get('metadata') or {}
        child = str(document.get('content', ''))
        parent = str(metadata.get('parent_content', ''))
        # Names and the actions they qualify can occur in different verses of the same paragraph.
        context = parent if parent and child.strip() and ' '.join(child.split()) in ' '.join(parent.split()) and ' '.join(parent.split()) != ' '.join(child.split()) else ''
        return str(metadata.get('heading', '')) + '\n' + child + '\n' + context

    @staticmethod
    def _metadata_match(
        metadata: Dict[str, Any], filters: Optional[Dict[str, Any]] = None
    ) -> bool:
        if not filters:
            return True
        if "$and" in filters:
            return all(
                VectorDatabase._metadata_match(metadata, clause)
                for clause in filters["$and"]
            )
        if "$or" in filters:
            return any(VectorDatabase._metadata_match(metadata, clause) for clause in filters["$or"])
        for key, expected in filters.items():
            actual = metadata.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$nin" in expected and actual in expected["$nin"]:
                    return False
                if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                    return False
                if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                    return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _to_chroma_where(
        filters: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not filters:
            return None
        if any(str(key).startswith("$") for key in filters):
            return filters
        clauses = [{key: value} for key, value in filters.items()]
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def _get_all_in_batches(
        self,
        *,
        include: List[str],
        where: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[Any]]:
        """Read a potentially large result set without exceeding SQLite variables."""
        batch_size = min(
            500,
            max(1, int(os.getenv("CHROMA_READ_BATCH_SIZE", "500"))),
        )
        combined: Dict[str, List[Any]] = {
            "ids": [],
            **{field: [] for field in include},
        }
        offset = 0

        while True:
            page = self.collection.get(
                where=where,
                include=include,
                limit=batch_size,
                offset=offset,
            )
            page_ids = list(page.get("ids") or [])
            if not page_ids:
                break

            combined["ids"].extend(page_ids)
            for field in include:
                values = page.get(field)
                if values is not None:
                    combined[field].extend(list(values))

            page_count = len(page_ids)
            offset += page_count
            if page_count < batch_size:
                break

        return combined

    def _delete_ids_in_batches(self, ids: List[str]) -> None:
        """Delete IDs in small batches to stay below SQLite's bind limit."""
        batch_size = min(
            500,
            max(1, int(os.getenv("CHROMA_ID_BATCH_SIZE", "500"))),
        )
        for start in range(0, len(ids), batch_size):
            self.collection.delete(ids=ids[start : start + batch_size])

    def _rebuild_bm25(self) -> None:
        with self._lock:
            results = self._get_all_in_batches(
                include=["documents", "metadatas"]
            )
            docs = results.get("documents") or []
            metas = results.get("metadatas") or [{} for _ in docs]
            ids = results.get("ids") or []

            self._documents = [
                {"id": doc_id, "content": text, "metadata": meta or {}}
                for doc_id, text, meta in zip(ids, docs, metas)
            ]
            self._scripture_headings = {}
            canonical_path = self.db_path / 'scripture.sqlite3'
            if canonical_path.is_file():
                with closing(sqlite3.connect(f'file:{canonical_path.as_posix()}?mode=ro', uri=True)) as connection:
                    self._scripture_headings = {(run, passage): {'heading':title,'heading_origin':origin or 'unknown'}
                        for run, passage, title, origin in connection.execute(
                        "SELECT run,json_extract(payload,'$.id'),json_extract(payload,'$.title'),json_extract(payload,'$.origin') FROM records WHERE kind='passage'") if title}
            for document in self._documents:
                meta = document['metadata']
                heading = self._scripture_headings.get((meta.get('ingest_run_id'),meta.get('passage_id')))
                if heading and meta.get('processing_version') == 4:
                    document['metadata'] = {**meta, **heading}
            self._name_aliases = build_name_aliases(docs)
            self._bm25_tokens = [
                self._tokenize(self._lexical_search_text(item)) for item in self._documents
            ]
            self._indexed_revision = (self.collection.metadata or {}).get("index_revision")
            if BM25Okapi is None or not self._bm25_tokens:
                self._bm25 = None
                return
            try:
                self._bm25 = BM25Okapi(self._bm25_tokens)
            except (ValueError, ZeroDivisionError):
                logger.exception("Unable to build BM25 index")
                self._bm25 = None

    @_with_access
    def rebuild_index(self) -> None:
        self._rebuild_bm25()

    @_with_access
    def add_vectors(
        self,
        texts: List[str],
        vectors: List[List[float]],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
        rebuild_index: bool = True,
    ) -> None:
        if not texts:
            raise ValueError("Danh sách văn bản đang rỗng.")
        if len(texts) != len(vectors):
            raise ValueError("Số lượng văn bản và vector không khớp.")
        if metadatas is None:
            metadatas = [{"source": "unknown"} for _ in texts]
        if len(metadatas) != len(texts):
            raise ValueError("Số lượng metadata và văn bản không khớp.")
        ids = ids or [str(uuid.uuid4()) for _ in texts]
        if len(ids) != len(texts):
            raise ValueError("Số lượng ID và văn bản không khớp.")

        self.ensure_collection_identity()
        dimensions = {len(vector) for vector in vectors}
        if dimensions != {self.embedding_dimension}:
            raise ValueError(
                "Embedding dimension does not match collection identity: "
                f"received {sorted(dimensions)}, expected {self.embedding_dimension}."
            )
        schema_versions = {
            int((metadata or {}).get("ingest_schema_version", 0))
            for metadata in metadatas
        }
        if schema_versions != {INGEST_SCHEMA_VERSION}:
            raise ValueError(
                "Ingest schema does not match collection identity: "
                f"received {sorted(schema_versions)}, expected "
                f"{INGEST_SCHEMA_VERSION}."
            )


        with self._lock:
            self.collection.add(
                embeddings=vectors,
                documents=texts,
                metadatas=metadatas,
                ids=ids,
            )
            if not self._pending_ingest():
                self._update_metadata(index_revision=uuid.uuid4().hex)
            if rebuild_index:
                self._rebuild_bm25()

    @_with_access
    def is_document_complete(
        self,
        document_id: str,
        required_schema_version: int | None = None,
        required_processing_version: int | None = None,
        required_metadata: dict | None = None,
    ) -> bool:
        results = self._get_all_in_batches(
            where={"document_id": document_id},
            include=["metadatas"],
        )
        metadatas = results.get("metadatas") or []
        if not metadatas:
            return False
        if required_metadata and any(any((m or {}).get(k) != v for k, v in required_metadata.items()) for m in metadatas):
            return False
        expected = int((metadatas[0] or {}).get("document_chunk_total", 0))
        complete = expected > 0 and len(metadatas) == expected and all(
            int((metadata or {}).get("document_chunk_total", 0)) == expected
            for metadata in metadatas
        )
        if required_processing_version is not None and any(
            (metadata or {}).get("processing_version") != required_processing_version
            for metadata in metadatas
        ):
            return False
        if not complete or required_schema_version is None:
            return complete
        return all(
            int((metadata or {}).get("ingest_schema_version", 0))
            == required_schema_version
            for metadata in metadatas
        )

    @_with_access
    def get_chunk_ids_by_source(self, source_name: str) -> List[str]:
        results = self._get_all_in_batches(
            where={"source": source_name},
            include=["metadatas"],
        )
        return list(results.get("ids") or [])

    @_with_access
    def get_document_source(self, document_id: str) -> Optional[str]:
        results = self.collection.get(
            where={"document_id": document_id}, include=["metadatas"], limit=1
        )
        metadatas = results.get("metadatas") or []
        if not metadatas:
            return None
        return (metadatas[0] or {}).get("source")

    @_with_access
    def delete_document(self, document_id: str, rebuild_index: bool = True) -> int:
        self.ensure_collection_ready()
        results = self._get_all_in_batches(
            where={"document_id": document_id},
            include=["metadatas"],
        )
        ids = list(results.get("ids") or [])
        if not ids:
            return 0
        with self._lock:
            self._delete_ids_in_batches(ids)
            self._update_metadata(index_revision=uuid.uuid4().hex)
            if rebuild_index:
                self._rebuild_bm25()
        return len(ids)

    @_with_access
    def delete_chunk_ids(self, ids: List[str], rebuild_index: bool = True) -> int:
        self.ensure_collection_ready()
        if not ids:
            return 0
        with self._lock:
            self._delete_ids_in_batches(ids)
            self._update_metadata(index_revision=uuid.uuid4().hex)
            if rebuild_index:
                self._rebuild_bm25()
        return len(ids)

    @_with_access
    def search_by_vector(
        self,
        query_vector: List[float],
        k: int = 3,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if self.count() == 0:
            return []
        self.ensure_collection_ready()
        query_args: Dict[str, Any] = {
            "query_embeddings": [query_vector],
            "n_results": min(k, self.count()),
            "include": ["documents", "metadatas", "distances"],
        }
        if self._indexed_revision != (self.collection.metadata or {}).get('index_revision'):
            self._rebuild_bm25()
        if filters:
            query_args["where"] = self._to_chroma_where(filters)
        results = self.collection.query(**query_args)
        documents = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]
        ids = (results.get("ids") or [[]])[0]
        metadatas = [{**(meta or {}), **heading}
                     if (heading := getattr(self,'_scripture_headings',{}).get(((meta or {}).get('ingest_run_id'),(meta or {}).get('passage_id')))) else meta
                     for meta in metadatas]
        return [
            {
                "id": doc_id,
                "content": content,
                "metadata": metadata or {},
                "distance": float(distance),
            }
            for doc_id, content, metadata, distance in zip(
                ids, documents, metadatas, distances
            )
        ]

    @_with_access
    def search_by_bm25(
        self,
        query_text: str,
        k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if not query_text:
            return []
        self.ensure_collection_ready()
        with self._lock:
            if self._bm25 is None or self._indexed_revision != (self.collection.metadata or {}).get("index_revision"):
                self._rebuild_bm25()
            if self._bm25 is None:
                query_tokens = set(self._tokenize(query_text))
                ranked = []
                for document in self._documents:
                    if not self._metadata_match(document["metadata"], filters):
                        continue
                    score = len(query_tokens.intersection(self._tokenize(self._lexical_search_text(document))))
                    if score:
                        ranked.append((float(score), document))
                ranked.sort(key=lambda item: item[0], reverse=True)
            else:
                scores = self._bm25.get_scores(self._tokenize(query_text))
                ranked = [
                    (float(scores[index]), document)
                    for index, document in enumerate(self._documents)
                    if scores[index] > 0
                    and self._metadata_match(document["metadata"], filters)
                ]
                ranked.sort(key=lambda item: item[0], reverse=True)

            return [
                {
                    "id": document["id"],
                    "content": document["content"],
                    "metadata": document["metadata"],
                    "bm25_raw_score": score,
                }
                for score, document in ranked[:k]
            ]

    @_with_access
    def search_by_metadata(
        self,
        filters: Dict[str, Any],
        k: Optional[int] = 20,
    ) -> List[Dict[str, Any]]:
        """Return exact metadata matches without semantic ranking."""
        if not filters or self.count() == 0:
            return []
        self.ensure_collection_ready()
        if k is None:
            results = self._get_all_in_batches(
                where=self._to_chroma_where(filters), include=["documents", "metadatas"],
            )
        else:
            results = self.collection.get(
                where=self._to_chroma_where(filters),
                include=["documents", "metadatas"],
                limit=min(max(1, int(k)), self.count()),
            )
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []
        ids = results.get("ids") or []
        return [
            {
                "id": doc_id,
                "content": content,
                "metadata": metadata or {},
            }
            for doc_id, content, metadata in zip(ids, documents, metadatas)
        ]

    @_with_access
    def hybrid_search(
        self,
        query_text: str,
        query_vector: List[float],
        k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        alpha: float = 0.5,
    ) -> List[Dict[str, Any]]:
        alpha = min(max(float(alpha), 0.0), 1.0)
        limit = max(k, 20)
        vector_results = self.search_by_vector(query_vector, limit, filters)
        bm25_results = self.search_by_bm25(query_text, limit, filters)
        rrf_k = max(1, int(os.getenv("RAG_RRF_K", "60")))

        vector_ranks = {
            item["id"]: rank
            for rank, item in enumerate(vector_results, start=1)
        }
        bm25_ranks = {
            item["id"]: rank
            for rank, item in enumerate(bm25_results, start=1)
        }
        vector_scores = {
            item["id"]: 1.0 / (1.0 + max(item["distance"], 0.0))
            for item in vector_results
        }
        bm25_raw = {
            item["id"]: item["bm25_raw_score"] for item in bm25_results
        }
        max_bm25 = max(bm25_raw.values(), default=0.0)
        bm25_scores = {
            doc_id: score / max_bm25 if max_bm25 > 0 else 0.0
            for doc_id, score in bm25_raw.items()
        }

        combined: Dict[str, Dict[str, Any]] = {}
        for item in vector_results + bm25_results:
            combined.setdefault(
                item["id"],
                {
                    "id": item["id"],
                    "content": item["content"],
                    "metadata": item["metadata"],
                },
            )

        merged = []
        for doc_id, item in combined.items():
            vector_rank = vector_ranks.get(doc_id)
            bm25_rank = bm25_ranks.get(doc_id)
            hybrid_score = 0.0
            if vector_rank is not None:
                hybrid_score += alpha / (rrf_k + vector_rank)
            if bm25_rank is not None:
                hybrid_score += (1.0 - alpha) / (rrf_k + bm25_rank)
            merged.append(
                {
                    **item,
                    "vector_score": vector_scores.get(doc_id, 0.0),
                    "bm25_score": bm25_scores.get(doc_id, 0.0),
                    "vector_rank": vector_rank,
                    "bm25_rank": bm25_rank,
                    "hybrid_score": hybrid_score,
                }
            )
        merged.sort(key=lambda item: item["hybrid_score"], reverse=True)
        return merged[:k]

    @_with_access
    def count(self) -> int:
        return self.collection.count()

    @_with_access
    def normalize_query_names(self, question: str) -> str:
        self.ensure_collection_ready()
        if self._indexed_revision != (self.collection.metadata or {}).get("index_revision"):
            self._rebuild_bm25()
        return normalize_query_names(question, self._name_aliases)

    @_with_access
    def get_search_vocabulary(self, question: str = '') -> List[str]:
        self.ensure_collection_ready()
        if self._indexed_revision != (self.collection.metadata or {}).get("index_revision"):
            self._rebuild_bm25()
        normalized = normalize_query_names(question, self._name_aliases)
        spellings = [normalized]
        for key, names in self._name_aliases.items():
            if len(names) > 1:
                for name in names:
                    variant = normalize_query_names(normalized, {key: (name,)})
                    if variant != normalized:
                        spellings.append(variant)
        subjects = list(dict.fromkeys(subject for spelling in spellings
            for subject in re.findall(r'\b[^\W\d_]+(?:-[^\W\d_]+)+\b', spelling)))
        extensions = []
        for subject in subjects:
            pattern = re.compile(r'\b'+re.escape(subject)+r'((?:\s+[^\W\d_]+(?:-[^\W\d_]+)*){0,2})\b')
            for row in self._documents:
                for match in pattern.finditer(row['content']):
                    parts = [subject]
                    for word in match[1].split():
                        if not word[0].isupper():
                            break
                        parts.append(word)
                    if len(parts)>1:
                        extensions.append(' '.join(parts))
        vocabulary = subjects + list(dict.fromkeys(extensions)) + [names[0] for names in self._name_aliases.values() if len(names)==1]
        return list(dict.fromkeys(vocabulary))[:80]

    @_with_access
    def get_all_documents(self) -> List[Dict[str, Any]]:
        self.ensure_collection_ready()
        if self._indexed_revision != (self.collection.metadata or {}).get("index_revision"):
            self._rebuild_bm25()
        with self._lock:
            return list(self._documents)

    @_with_access
    def get_source_catalog(self) -> dict:
        self.ensure_collection_ready()
        rows = self._get_all_in_batches(include=["metadatas"])
        catalog = {}
        for metadata in rows.get("metadatas") or []:
            metadata = metadata or {}
            source = metadata.get("source")
            if source:
                kind = metadata.get("document_type", "generic")
                catalog[source] = {"document_type": "scripture" if kind == "bible" else kind,
                                   "edition_id": metadata.get("edition_id", ""), "translation": metadata.get("translation", ""),
                                   "document_id": metadata.get("document_id", ""), "ingest_run_id": metadata.get("ingest_run_id", ""),
                                   "processing_version": metadata.get("processing_version", 0)}
        return catalog

    @_with_access
    def reusable_scripture_vectors(self, source, edition_id):
        self.ensure_collection_identity()
        rows = self._get_all_in_batches(include=["documents", "metadatas", "embeddings"],
                                       where={"$and": [{"source": source}, {"edition_id": edition_id}]})
        return {(meta.get("book_code"), meta.get("chapter"), meta.get("verse_label"), text): vector.tolist()
                for text, meta, vector in zip(rows["documents"], rows["metadatas"], rows["embeddings"])
                if meta.get("document_type") in {"scripture", "bible"}}

    @_with_access
    def get_all_sources(self) -> List[str]:
        self.ensure_collection_ready()
        results = self._get_all_in_batches(include=["metadatas"])
        sources = {
            metadata.get("source")
            for metadata in results.get("metadatas") or []
            if metadata and metadata.get("source")
        }
        return sorted(sources, key=str.casefold)

    @_with_access
    def get_document_content(self, source_name: str) -> str:
        self.ensure_collection_ready()
        if self.get_source_catalog().get(source_name, {}).get("document_type") == "scripture":
            from loaddocs.scripture_store import ScriptureStore
            from loaddocs.scripture_workflow import coordinate
            store = ScriptureStore(self)
            return "\n\n".join(f"{coordinate(v)}\n{v.get('display_text', v['text'])}"
                               for run, meta, _profile, _common in store.active()
                               if meta.get("source") == source_name for v in store.records(run))
        results = self._get_all_in_batches(
            where={"source": source_name},
            include=["documents", "metadatas"],
        )
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or [{} for _ in documents]
        parent_chunks: Dict[str, Dict[str, Any]] = {}

        for index, (document, metadata) in enumerate(zip(documents, metadatas)):
            metadata = metadata or {}
            parent_id = str(metadata.get("parent_id") or f"legacy-{index}")
            parent_chunks.setdefault(
                parent_id,
                {
                    "page": int(metadata.get("page", 0) or 0),
                    "section": int(metadata.get("section_index", 0) or 0),
                    "index": int(
                        metadata.get("parent_index", metadata.get("chunk_index", index))
                        or 0
                    ),
                    "content": metadata.get("parent_content") or document,
                },
            )

        pages: Dict[int, List[Dict[str, Any]]] = {}
        for item in parent_chunks.values():
            pages.setdefault(item["page"], []).append(item)
        page_texts = []
        for page in sorted(pages):
            ordered = sorted(
                pages[page],
                key=lambda item: (item["section"], item["index"]),
            )
            page_texts.append(_merge_overlapping_text([item["content"] for item in ordered]))
        return "\n\n".join(text for text in page_texts if text)

    @_with_access
    def reset(self) -> None:
        with self._lock:
            self.client.delete_collection(self.collection.name)
            self.collection = self._get_or_create_collection()
            self._rebuild_bm25()

    @_with_access
    def delete_data_select(self, source_name: str) -> int:
        ids = self.get_chunk_ids_by_source(source_name)
        return self.delete_chunk_ids(ids)


@lru_cache(maxsize=1)
def get_vector_db() -> VectorDatabase:
    collection_name = os.getenv("CHROMA_COLLECTION", "rag_vietnamese_embedding_v2_v1")
    return VectorDatabase(collection_name=collection_name)


default_db = get_vector_db()
