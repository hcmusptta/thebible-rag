import os
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch, ANY

from loaddocs.document import DocumentProcessor
from services.ans import (
    _collect_hybrid_candidates,
    _rerank_with_cross_encoder,
    _select_context_content,
    retrieve_ranked_contexts,
)
from services.eval_ragas import _summarize_retrieval_diagnostics
from vectordb.vector_store import VectorDatabase


class ReciprocalRankFusionTests(unittest.TestCase):
    def test_document_seen_by_both_retrievers_ranks_first(self) -> None:
        database = object.__new__(VectorDatabase)
        database.access = nullcontext
        metadata = {"source": "test"}
        database.search_by_vector = lambda *_args, **_kwargs: [
            {"id": "a", "content": "A", "metadata": metadata, "distance": 0.1},
            {"id": "b", "content": "B", "metadata": metadata, "distance": 0.2},
        ]
        database.search_by_bm25 = lambda *_args, **_kwargs: [
            {"id": "b", "content": "B", "metadata": metadata, "bm25_raw_score": 5.0},
            {"id": "c", "content": "C", "metadata": metadata, "bm25_raw_score": 4.0},
        ]

        with patch.dict(os.environ, {"RAG_RRF_K": "60"}):
            results = database.hybrid_search("query", [0.0], k=3)

        self.assertEqual("b", results[0]["id"])
        self.assertEqual((2, 1), (
            results[0]["vector_rank"],
            results[0]["bm25_rank"],
        ))

    def test_nested_verse_range_filter(self) -> None:
        filters = {"$and": [
            {"book_code": "MAT"},
            {"chapter": 5},
            {"verse_start": {"$lte": 12}},
            {"verse_end": {"$gte": 1}},
        ]}

        self.assertTrue(VectorDatabase._metadata_match(
            {"book_code": "MAT", "chapter": 5, "verse_start": 10, "verse_end": 10},
            filters,
        ))
        self.assertFalse(VectorDatabase._metadata_match(
            {"book_code": "MAT", "chapter": 5, "verse_start": 20, "verse_end": 20},
            filters,
        ))


class RetrievalSelectionTests(unittest.TestCase):
    def test_complete_parent_or_complete_child_is_preserved(self):
        child = "A complete sentence. " * 30
        parent = "Introduction. " + child + "Additional evidence."
        self.assertEqual(parent, _select_context_content({
            "content": child, "metadata": {"parent_content": parent},
        }))
        self.assertEqual(child.strip(), _select_context_content({
            "content": child, "metadata": {"parent_content": "unrelated parent"},
        }))

    def test_low_scores_keep_distinct_complete_parents_and_source_ranges(self):
        parent = "Before. " * 80 + "verse one. verse two. " + "After. " * 80
        metadata = {"parent_id": "p1", "parent_content": parent,
                    "verse_start": 2, "verse_end": 2,
                    "parent_verse_start": 1, "parent_verse_end": 5}
        candidates = [
            {"id": "v2", "content": "verse two.", "metadata": metadata, "rerank_score": 0.4},
            {"id": "v1", "content": "verse one.", "metadata": metadata, "rerank_score": 0.3},
            {"id": "other", "content": "Other evidence.", "metadata": {}, "rerank_score": 0.1},
        ]
        diagnostics = {}
        with (
            patch("services.ans.extract_scripture_query_filters", return_value={}),
            patch("services.ans._collect_hybrid_candidates", return_value=("resolved question", candidates)),
            patch("services.ans._rerank_with_cross_encoder", return_value=candidates) as rerank,
            patch.dict(os.environ, {"RERANK_MIN_SCORE": "0.5", "RAG_CONTEXT_MAX_CHARS": "500"}),
        ):
            results = retrieve_ranked_contexts("follow up", top_k=2, diagnostics=diagnostics)
        self.assertEqual(2, len(results))
        self.assertEqual(parent.strip(), results[0]["content"])
        self.assertEqual("Other evidence.", results[1]["content"])
        self.assertEqual((1, 5), (results[0]["metadata"]["verse_start"], results[0]["metadata"]["verse_end"]))
        self.assertEqual(2, metadata["verse_start"])
        self.assertEqual("resolved question", rerank.call_args.args[0])
        self.assertEqual(2, diagnostics["returned_context_count"])

    def test_reranker_sees_matched_child_and_parent_context(self):
        parent = "early " + "other text " * 100 + "late"
        candidates = [
            {"id": "early", "content": "early", "metadata": {"parent_id": "p", "parent_content": parent}},
            {"id": "late", "content": "late", "metadata": {"parent_id": "p", "parent_content": parent}},
        ]
        reranker = Mock()
        reranker.predict.return_value = [0.1, 0.4]
        reranker.max_length=4096
        reranker.tokenizer.encode.side_effect=lambda text,**kwargs: list(text)
        reranker.tokenizer.num_special_tokens_to_add.return_value=3
        with (
            patch("services.ans._get_reranker", return_value=reranker),
            patch.dict(os.environ, {"RERANK_ENABLED": "true"}),
        ):
            results = _rerank_with_cross_encoder("query", candidates)
        self.assertEqual([("query", "early\n\n" + parent), ("query", "late\n\n" + parent)], reranker.predict.call_args.args[0])
        self.assertEqual("late", results[0]["id"])
        with (
            patch("services.ans._get_reranker") as factory,
            patch.dict(os.environ, {"RERANK_ENABLED": "false"}),
        ):
            self.assertEqual(candidates, _rerank_with_cross_encoder("query", candidates))
            factory.assert_not_called()

    def test_standalone_chat_and_benchmark_plan_without_rewriting_twice(self):
        for benchmark in (False, True):
            with (
                self.subTest(benchmark=benchmark),
                patch("services.ans.get_llm_service") as chat_llm,
                patch("services.ans.get_benchmark_llm_service") as benchmark_llm,
                patch("services.ans.get_default_embedder") as embedder,
                patch("services.ans.default_db.hybrid_search", return_value=[]) as search,
            ):
                embedder.return_value.embed_query.return_value = [0.0]
                query, results = _collect_hybrid_candidates(
                    "original", chat_history=[{"role": "user", "content": "original"}],
                    candidate_limit=5, is_benchmark=benchmark,
                )
                self.assertEqual(("original", []), (query, results))
                search.assert_called_once_with("original", [0.0], k=5, filters=None)
                (benchmark_llm if benchmark else chat_llm).return_value.plan_retrieval.assert_called_once_with("original", [], ANY)
                (chat_llm if benchmark else benchmark_llm).assert_not_called()

    def test_followup_rewrites_once_using_prior_history(self):
        history = [{"role": "user", "content": "Who is Moses?"},
                   {"role": "assistant", "content": "Moses is ..."}]
        with (
            patch("services.ans.get_llm_service") as llm,
            patch("services.ans.get_default_embedder") as embedder,
            patch("services.ans.default_db.hybrid_search", return_value=[]) as search,
        ):
            llm.return_value.plan_retrieval.return_value = {"question":"What did Moses do?", "queries":[]}
            embedder.return_value.embed_query.return_value = [0.0]
            query, _ = _collect_hybrid_candidates(
                "What did he do?", chat_history=history + [{"role": "user", "content": "What did he do?"}],
            )
            llm.return_value.plan_retrieval.assert_called_once_with("What did he do?", history, ANY)
            self.assertEqual("What did Moses do?", query)
            self.assertEqual(["What did he do?", query], [c.args[0] for c in search.call_args_list])


class RetrievalDiagnosticsTests(unittest.TestCase):
    def test_summarizes_counts_and_hit_rates(self) -> None:
        rows = [
            {"retrieval_diagnostics": {
                "hybrid_candidate_count": 40,
                "rerank_candidate_count": 40,
                "returned_context_count": 2,
                "hybrid_exact_hit_at_k": False,
                "rerank_exact_hit_at_k": True,
                "final_exact_hit_at_k": True,
            }},
            {"retrieval_diagnostics": {
                "hybrid_candidate_count": 20,
                "rerank_candidate_count": 20,
                "returned_context_count": 1,
                "hybrid_exact_hit_at_k": True,
                "rerank_exact_hit_at_k": True,
                "final_exact_hit_at_k": False,
            }},
        ]

        summary = _summarize_retrieval_diagnostics(rows)

        self.assertEqual(30.0, summary["hybrid_candidates_avg"])
        self.assertEqual(0.5, summary["hybrid_exact_hit_at_k"])
        self.assertEqual(1.0, summary["rerank_exact_hit_at_k"])


if __name__ == "__main__":
    unittest.main()
