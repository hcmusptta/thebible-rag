import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from loaddocs import embedding
from loaddocs.scripture import extract_scripture_query_filters
from services import ans, eval_ragas
from vectordb.vector_store import VectorDatabase


class ExactScriptureTests(unittest.TestCase):
    def test_vietnamese_d_and_unicode_dashes_keep_exact_range(self):
        expected = extract_scripture_query_filters("Dnl 5:1-3")
        for query in ("Đnl 5:1-3", "Đnl 5:1–3", "Đnl 5:1—3", "Đnl 5:1−3"):
            with self.subTest(query=query):
                self.assertEqual(expected, extract_scripture_query_filters(query))
        self.assertIn("$and", expected)

class ContextSelectionTests(unittest.TestCase):
    def test_substantial_generic_child_gets_complete_parent_context(self):
        child = "A complete child sentence. " * 7
        parent = "Before. " * 70 + child + " After." * 70
        content = ans._select_context_content({
            "content": child, "metadata": {"parent_content": parent},
        })
        self.assertIn(child.strip(), content)
        self.assertGreater(len(content), len(child.strip()))
        self.assertEqual(parent, content)

    def test_distinct_children_in_same_parent_are_both_reranked(self):
        parent = "early " + "x " * 600 + "late"
        candidates = [
            {"id": "early", "content": "early", "metadata": {"parent_id": "p", "parent_content": parent}},
            {"id": "late", "content": "late", "metadata": {"parent_id": "p", "parent_content": parent}},
        ]
        model = Mock()
        model.predict.return_value = [0.1, 0.9]
        model.max_length=4096
        model.tokenizer.encode.side_effect=lambda text,**kwargs: list(text)
        model.tokenizer.num_special_tokens_to_add.return_value=3
        with patch.object(ans, "_get_reranker", return_value=model):
            result = ans._rerank_with_cross_encoder("query", candidates)
        self.assertEqual(2, len(model.predict.call_args.args[0]))
        self.assertEqual("late", result[0]["id"])

    def test_no_context_means_no_answer_generation(self):
        with patch.object(ans, "retrieve_ranked_contexts", return_value=[]), patch.object(ans, "get_llm_service") as llm:
            answer, contexts = ans.answer_question("unrelated question")
        llm.assert_not_called()
        self.assertTrue(answer)
        self.assertEqual([], contexts)


class SharedEmbeddingTests(unittest.TestCase):
    def test_ragas_reuses_app_embedder_and_alternative_defaults_to_cpu(self):
        shared = Mock()
        with (
            patch.dict(os.environ, {"EMBEDDING_MODEL_NAME": "same-model", "RAGAS_EMBEDDING_MODEL": "same-model",
                                     "RAGAS_EMBEDDING_DEVICE": "cpu"}),
            patch.object(eval_ragas, "get_default_embedder", return_value=shared),
            patch.object(eval_ragas, "get_embedder", return_value=shared) as load,
        ):
            self.assertIs(shared, eval_ragas._build_ragas_embeddings())
            load.assert_not_called()
            with patch.dict(os.environ, {"RAGAS_EMBEDDING_MODEL": "different-model"}):
                self.assertIs(shared, eval_ragas._build_ragas_embeddings())
            load.assert_called_once_with("different-model", device="cpu")

    def test_concurrent_first_use_loads_model_once(self):
        embedding._load_embedder.cache_clear()
        try:
            with patch.object(embedding, "TextEmbedder") as factory:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    models = list(pool.map(lambda _: embedding.get_embedder("test/model", "cpu"), range(8)))
                factory.assert_called_once()
                self.assertTrue(all(item is models[0] for item in models))
        finally:
            embedding._load_embedder.cache_clear()

    def test_shared_embedder_supports_ragas_usage_events_sync_and_async(self):
        from ragas.embeddings import LangchainEmbeddingsWrapper

        model = Mock()
        model.encode.side_effect = lambda texts, **kwargs: (
            np.array([1.0, 0.0, 0.0]) if isinstance(texts, str)
            else np.array([[1.0, 0.0, 0.0] for _ in texts])
        )
        with patch.object(embedding, "SentenceTransformer", return_value=model):
            embedder = embedding.TextEmbedder("test/model", device="cpu")
        with patch("ragas.embeddings.base.track") as track:
            wrapped = LangchainEmbeddingsWrapper(embedder)
            self.assertEqual([1.0, 0.0, 0.0], wrapped.embed_query("query"))
            self.assertEqual([[1.0, 0.0, 0.0]], wrapped.embed_documents(["context"]))
            self.assertEqual([1.0, 0.0, 0.0], asyncio.run(wrapped.aembed_query("query")))
            self.assertEqual([[1.0, 0.0, 0.0]], asyncio.run(wrapped.aembed_documents(["context"])))
        self.assertEqual(
            ["test/model"] * 4,
            [call.args[0].model for call in track.call_args_list],
        )


if __name__ == "__main__":
    unittest.main()
