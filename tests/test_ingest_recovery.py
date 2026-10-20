import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

import fitz

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from services.readdocs import IngestCancelledError, process_uploaded_pdf
from vectordb.vector_store import VectorDatabase


def add_version(db, name, *, run_id=None, expected=2, count=2):
    metadata = {
        "source": "sample.pdf", "document_id": name, "document_chunk_total": expected,
        "ingest_schema_version": 5,
    }
    if run_id:
        metadata["ingest_run_id"] = run_id
    db.add_vectors(
        texts=[f"{name} text {index}" for index in range(count)],
        vectors=[[1.0, 0.0, 0.0] for _ in range(count)],
        ids=[f"{name}-{index}" for index in range(count)],
        metadatas=[dict(metadata) for _ in range(count)],
        rebuild_index=False,
    )


def crash_worker(path, phase):
    db = VectorDatabase("ingest-test", db_path=path)
    with db.access():
        run_id = db.begin_ingest("new", "sample.pdf", 2)
        add_version(db, "new", run_id=run_id, count=1 if phase == "writing" else 2)
        if phase == "writing":
            os._exit(41)
        delete = db._delete_ids_in_batches

        def interrupted_delete(ids):
            delete(ids[:1])
            os._exit(42)

        db._delete_ids_in_batches = interrupted_delete
        db.commit_ingest()


class FakeEmbedder:
    def __init__(self, fail_on=None, cancel_event=None):
        self.calls = 0
        self.fail_on = fail_on
        self.cancel_event = cancel_event

    def embed_documents(self, texts):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("simulated embedding failure")
        if self.cancel_event is not None and self.calls == 2:
            self.cancel_event.set()
        return [[1.0, 0.0, 0.0] for _ in texts]


class IngestRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {
            "EMBEDDING_MODEL_NAME": "test/embedding", "EMBEDDING_DIMENSION": "3",
            "EMBEDDING_BATCH_SIZE": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = str(Path(self.temp.name) / "db")
        self.db = VectorDatabase("ingest-test", db_path=self.path)
        self.addCleanup(lambda: self.db.client.close())

    def seed(self):
        add_version(self.db, "old")
        self.db.set_collection_state("ready")
        self.db.rebuild_index()

    def make_pdf(self):
        path = Path(self.temp.name) / "new.pdf"
        with fitz.open() as pdf:
            for index in range(2):
                page = pdf.new_page()
                page.insert_text((72, 72), (f"Page {index} new document content. " * 4 + "\n") * 5)
            pdf.save(path)
        return str(path)

    def assert_version(self, name):
        docs = self.db.get_all_documents()
        self.assertTrue(docs)
        self.assertEqual({name}, {d["metadata"]["document_id"] for d in docs})
        self.assertEqual("ready", self.db.collection.metadata["state"])
        self.assertNotIn("ingest_transaction", self.db.collection.metadata)

    def test_real_pdf_replaces_old_version_and_duplicate_is_ready(self):
        self.seed()
        path = self.make_pdf()
        with patch("services.readdocs.get_default_embedder", return_value=FakeEmbedder()):
            result = process_uploaded_pdf(path, "sample.pdf", self.db)
            duplicate = process_uploaded_pdf(path, "sample.pdf", self.db)
        self.assertGreater(result.chunk_count, 1)
        self.assertEqual(2, result.replaced_chunk_count)
        self.assertTrue(duplicate.duplicate)
        self.assert_version(result.document_id)
        self.assertEqual(result.chunk_count, self.db.count())

    def test_failed_embedding_and_cancellation_both_keep_old_version(self):
        self.seed()
        path = self.make_pdf()
        cancel_event = threading.Event()
        for embedder, error in (
            (FakeEmbedder(fail_on=2), RuntimeError),
            (FakeEmbedder(cancel_event=cancel_event), IngestCancelledError),
        ):
            with self.subTest(error=error.__name__):
                cancel_event.clear()
                with (
                    patch("services.readdocs.get_default_embedder", return_value=embedder),
                    self.assertRaises(error),
                ):
                    process_uploaded_pdf(path, "sample.pdf", self.db, cancel_event=cancel_event)
                self.assert_version("old")
                self.assertEqual(2, self.db.count())

    def test_failed_old_version_deletion_stays_unavailable_until_recovery(self):
        self.seed()
        path = self.make_pdf()
        with (
            patch("services.readdocs.get_default_embedder", return_value=FakeEmbedder()),
            patch.object(self.db, "_delete_ids_in_batches", side_effect=OSError("disk busy")),
            self.assertRaises(OSError),
        ):
            process_uploaded_pdf(path, "sample.pdf", self.db)
        with self.assertRaisesRegex(RuntimeError, "chưa hoàn tất"):
            self.db.search_by_vector([1.0, 0.0, 0.0])
        self.db.recover_pending_ingest()
        self.assertEqual("ready", self.db.collection.metadata["state"])
        self.assertNotIn("old", {d["metadata"]["document_id"] for d in self.db.get_all_documents()})

    def test_hard_exit_recovers_writing_and_half_finished_commit_in_new_process(self):
        for phase, code, expected in (("writing", 41, "old"), ("committing", 42, "new")):
            with self.subTest(phase=phase):
                self.db.reset()
                self.seed()
                self.db.client.close()
                env = dict(os.environ, CHROMA_DB_PATH=self.path, CHROMA_COLLECTION="unused-default")
                completed = subprocess.run(
                    [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--crash", self.path, phase],
                    cwd=APP_ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=90,
                )
                self.assertEqual(code, completed.returncode, completed.stderr)
                self.db = VectorDatabase("ingest-test", db_path=self.path)
                self.assert_version(expected)
                self.assertEqual(2, self.db.count())

    def test_pre_journal_complete_collection_can_reopen_but_partial_is_blocked(self):
        add_version(self.db, "old")
        self.db.client.close()
        self.db = VectorDatabase("ingest-test", db_path=self.path)
        self.assert_version("old")
        self.db.reset()
        add_version(self.db, "partial", count=1)
        with self.assertRaisesRegex(RuntimeError, "nhật ký"):
            self.db.recover_pending_ingest()
        self.assertEqual(1, self.db.count())
        self.assertEqual("building", self.db.collection.metadata["state"])

    def test_other_instance_refreshes_bm25_after_commit(self):
        self.seed()
        other = VectorDatabase("ingest-test", db_path=self.path)
        try:
            with self.db.access():
                run_id = self.db.begin_ingest("new", "sample.pdf", 2)
                add_version(self.db, "new", run_id=run_id)
                self.db.commit_ingest()
            self.assertEqual({"new"}, {d["metadata"]["document_id"] for d in other.get_all_documents()})
        finally:
            other.client.close()

    def test_active_ingest_blocks_other_instance_and_thread(self):
        from concurrent.futures import ThreadPoolExecutor

        self.seed()
        other = VectorDatabase("ingest-test", db_path=self.path)
        try:
            with self.db.access(), ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaisesRegex(RuntimeError, "tiến trình khác"):
                    other.count()
                with self.assertRaisesRegex(RuntimeError, "đang bận"):
                    pool.submit(self.db.count).result(timeout=5)
            self.assertEqual(2, other.count())
        finally:
            other.client.close()


class IngestCancellationUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_collection_is_reported_without_calling_llm(self):
        with patch("services.runtime_config.write_public_runtime_config"):
            import main
        with (
            patch.object(main.default_db, "count", side_effect=main.CollectionUnavailableError("Kho đang bận")),
            patch.object(main, "_send_home_message", new_callable=AsyncMock) as send,
            patch.object(main, "answer_question") as answer,
        ):
            await main._answer("Câu hỏi kiểm tra")
        send.assert_awaited_once_with("Kho đang bận")
        answer.assert_not_called()

    async def test_busy_during_retrieval_is_not_reported_as_llm_failure(self):
        with patch("services.runtime_config.write_public_runtime_config"):
            import main
        history = []
        response = Mock(send=AsyncMock(), update=AsyncMock())
        with (
            patch.object(main.default_db, "count", return_value=2),
            patch.object(main, "_history", return_value=history),
            patch.object(main.cl.user_session, "get", return_value={}),
            patch.object(main.cl, "Message", return_value=response),
            patch.object(main, "_main_actions", return_value=[]),
            patch.object(main, "answer_question", side_effect=main.CollectionUnavailableError("Kho đang bận")),
        ):
            await main._answer("Câu hỏi kiểm tra")
        self.assertEqual("Kho đang bận", response.content)
        self.assertEqual([], history)
        response.update.assert_awaited_once()

    async def test_cancel_waits_for_worker_to_acknowledge_rollback(self):
        with patch("services.runtime_config.write_public_runtime_config"):
            import main
        started = threading.Event()
        stopped = threading.Event()

        def ingest(*args, cancel_event, **kwargs):
            started.set()
            if not cancel_event.wait(timeout=5):
                raise RuntimeError("Cancellation was not delivered.")
            stopped.set()
            raise IngestCancelledError()

        with patch.object(main, "process_uploaded_document", side_effect=ingest):
            task = asyncio.create_task(main._run_ingest_with_progress(
                Mock(path="unused.pdf", name="sample.pdf"), Mock(update=AsyncMock()),
            ))
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(stopped.is_set())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--crash":
        crash_worker(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
