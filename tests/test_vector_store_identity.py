import tempfile
import unittest
from unittest.mock import patch

import chromadb

from vectordb.vector_store import VectorDatabase


class VectorStoreIdentityTests(unittest.TestCase):
    def test_new_collection_records_identity_and_rejects_wrong_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as db_path, patch.dict(
            "os.environ",
            {
                "EMBEDDING_MODEL_NAME": "test/embedding-model",
                "EMBEDDING_DIMENSION": "3",
            },
        ):
            database = VectorDatabase("identity-test", db_path=db_path)
            try:
                self.assertEqual(
                    {
                        "hnsw:space": "cosine",
                        "state": "building",
                        "embedding_model": "test/embedding-model",
                        "embedding_dimension": 3,
                        "ingest_schema_version": 5,
                    },
                    database.collection.metadata,
                )
                with self.assertRaisesRegex(ValueError, "Embedding dimension"):
                    database.add_vectors(
                        texts=["text"],
                        vectors=[[0.0, 0.0]],
                        metadatas=[{"ingest_schema_version": 5}],
                        ids=["chunk-1"],
                    )

                database.add_vectors(
                    texts=["text"],
                    vectors=[[1.0, 0.0, 0.0]],
                    metadatas=[{"ingest_schema_version": 5}],
                    ids=["chunk-1"],
                )
                with self.assertRaisesRegex(RuntimeError, "chưa hoàn tất"):
                    database.search_by_vector([1.0, 0.0, 0.0])
                database.set_collection_state("ready")
                self.assertEqual(
                    1, len(database.search_by_vector([1.0, 0.0, 0.0]))
                )
            finally:
                database.client.close()

    def test_nonempty_legacy_collection_is_read_only_for_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as db_path, patch.dict(
            "os.environ",
            {
                "EMBEDDING_MODEL_NAME": "test/embedding-model",
                "EMBEDDING_DIMENSION": "2",
            },
        ):
            client = chromadb.PersistentClient(path=db_path)
            database = None
            try:
                collection = client.create_collection(
                    "legacy-test", metadata={"hnsw:space": "cosine"}
                )
                collection.add(
                    ids=["legacy-1"],
                    documents=["legacy"],
                    embeddings=[[0.0, 0.0]],
                )

                database = VectorDatabase("legacy-test", db_path=db_path)
                with self.assertRaisesRegex(RuntimeError, "new CHROMA_COLLECTION"):
                    database.ensure_collection_identity()
            finally:
                if database is not None:
                    database.client.close()
                client.close()


if __name__ == "__main__":
    unittest.main()
