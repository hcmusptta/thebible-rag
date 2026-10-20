import os
import tempfile
import unittest
import unicodedata
from unittest.mock import Mock, patch, ANY

from services.ans import _collect_hybrid_candidates, retrieve_ranked_contexts
from services.model import LLMService
from services.query_normalization import build_name_aliases, normalize_query_names
from vectordb.vector_store import VectorDatabase


CORPUS = "Ông A-đam, Mô-sê, Áp-ra-ham, Giê-su, Ma-ri-a, Phê-rô, Ða-vít, Giê-ru-sa-lem, Na-bu-cô-đô-nô-xo. Giu-se, Gio-an, Phao-lô, Nô-ê, Sa-lô-môn, I-sa-ác, Gia-cóp, Ê-li-a, Giô-suê, Sa-mu-en, A-ha-ron, Đa-ni-en, E-và, Sa-ra, Rê-bê-ca, Ra-khen, Giu-đa, Mác-cô, Lu-ca, Ti-mô-thê, Ê-li-sa-bét, Da-ca-ri-a."
ALIASES = build_name_aliases([CORPUS])


class QueryNormalizationTests(unittest.TestCase):
    def test_known_name_variants_preserve_question_and_references(self):
        for name in ("a dam", "a-dam", "adam", "A-đam", "A ĐAM", "a–đam", "A — DAM", "a  dam"):
            with self.subTest(name=name):
                question = f"Theo St 5:1–5, {name} sống bao lâu?"
                normalized = normalize_query_names(unicodedata.normalize("NFD", question), ALIASES)
                self.assertEqual("Theo St 5:1–5, A-đam sống bao lâu?", normalized)
                self.assertEqual(normalized, normalize_query_names(normalized, ALIASES))

    def test_names_are_learned_from_translation_without_manual_entries(self):
        for raw, expected in (
            ("mo se", "Mô-sê"), ("Mose", "Mô-sê"), ("Mô sê", "Mô-sê"),
            ("ap ra ham", "Áp-ra-ham"), ("apraham", "Áp-ra-ham"),
            ("giesu", "Giê-su"), ("Giê su", "Giê-su"), ("Giê Su", "Giê-su"),
            ("giê-su", "Giê-su"), ("GIÊ SU", "Giê-su"), ("Giê — Su", "Giê-su"),
            ("ma ri a", "Ma-ri-a"), ("Maria", "Ma-ri-a"),
            ("phe ro", "Phê-rô"), ("phero", "Phê-rô"),
            ("da vit", "Ða-vít"), ("Đa vít", "Ða-vít"),
            ("gie ru sa lem", "Giê-ru-sa-lem"), ("Gierusalem", "Giê-ru-sa-lem"),
            ("na bu co do no xo", "Na-bu-cô-đô-nô-xo"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(expected + " là ai?", normalize_query_names(raw + " là ai?", ALIASES))
        self.assertEqual("mo se là ai?", normalize_query_names("mo se là ai?", {}))
        learned = build_name_aliases(["Ông Mê-phi-bô-sét."])
        self.assertEqual("Mê-phi-bô-sét", normalize_query_names("me phi bo set", learned))

    def test_multiple_names_longest_match_and_punctuation(self):
        self.assertEqual(
            "Mô-sê và Giê-su; xem Ga 1:1–3.",
            normalize_query_names("mo se và giesu; xem Ga 1:1–3.", ALIASES),
        )
        aliases = build_name_aliases(["A-bi. A-bi-gia."])
        self.assertEqual("A-bi-gia", normalize_query_names("a bi gia", aliases))
        for raw in ("mo, se", "mo 5 se", "Moseley", "mo-se-xyz", "xyz-mo-se", "1giesu", "user_giesu"):
            with self.subTest(raw=raw):
                self.assertEqual(raw, normalize_query_names(raw, ALIASES))

    def test_ambiguous_names_and_conflicting_accents_are_not_guessed(self):
        aliases = build_name_aliases(["E-den. Ê-đen. Ða-vít. Đa-vít. MÔ-SÊ. Mô-sê."])
        self.assertEqual("e den", normalize_query_names("e den", aliases))
        self.assertEqual("Ê-đen", normalize_query_names("ê đen", aliases))
        self.assertEqual(1, len(aliases["davit"]))
        self.assertEqual("Mô-sê", normalize_query_names("mo se", aliases))
        self.assertEqual("má ri a", normalize_query_names("má ri a", ALIASES))
        self.assertEqual({}, build_name_aliases(["bình-thường, 1-3, mo-se"]))

    def test_unrelated_words_and_names_are_unchanged(self):
        for question in ("", "madam", "Adams", "adamant", "Mô-sê là ai?", "Đnl 5:1–3", "á đạm"):
            with self.subTest(question=question):
                self.assertEqual(question, normalize_query_names(question, ALIASES))

    def test_variants_share_normalization_with_planned_retrieval(self):
        canonical = "A-đam là ai?"
        candidates = [{"id": "adam", "content": "Ông A-đam.", "metadata": {}}]
        for benchmark in (False, True):
            for name in ("a dam", "a-dam", "adam", "A-đam"):
                question = f"{name} là ai?"
                history = [{"role": "user", "content": question}]
                diagnostics = {}
                with (
                    self.subTest(name=name, benchmark=benchmark),
                    patch("services.ans.get_llm_service") as chat_llm,
                    patch("services.ans.get_benchmark_llm_service") as benchmark_llm,
                    patch("services.ans.default_db.normalize_query_names", side_effect=lambda q: normalize_query_names(q, ALIASES)),
                    patch("services.ans.get_default_embedder") as embedder,
                    patch("services.ans.default_db.hybrid_search", return_value=candidates) as search,
                    patch("services.ans._rerank_with_cross_encoder", return_value=candidates) as rerank,
                ):
                    embedder.return_value.embed_query.return_value = [0.0]
                    results = retrieve_ranked_contexts(
                        question, chat_history=history, is_benchmark=benchmark,
                        diagnostics=diagnostics,
                    )
                    self.assertEqual("adam", results[0]["id"])
                    embedder.return_value.embed_query.assert_called_once_with(canonical)
                    search.assert_called_once()
                    self.assertEqual((canonical, [0.0]), search.call_args.args)
                    self.assertEqual(20, search.call_args.kwargs['k'])
                    from vectordb.vector_store import VectorDatabase
                    filters = search.call_args.kwargs['filters']
                    self.assertFalse(VectorDatabase._metadata_match({'document_type':'scripture','processing_version':3}, filters))
                    self.assertTrue(VectorDatabase._metadata_match({'document_type':'scripture','processing_version':4}, filters))
                    self.assertEqual(canonical, rerank.call_args.args[0])
                    self.assertEqual(canonical, diagnostics["retrieval_query"])
                    self.assertEqual(question != canonical, diagnostics["query_normalized"])
                    self.assertEqual([{"role": "user", "content": question}], history)
                    (benchmark_llm if benchmark else chat_llm).return_value.plan_retrieval.assert_called_once_with(canonical, [], ANY)
                    (chat_llm if benchmark else benchmark_llm).assert_not_called()

    def test_followup_normalizes_name_after_history_rewrite(self):
        history = [{"role": "user", "content": "a dam là ai?"},
                   {"role": "assistant", "content": "Ông A-đam."}]
        with (
            patch("services.ans.get_llm_service") as llm,
            patch("services.ans.default_db.normalize_query_names", side_effect=lambda q: normalize_query_names(q, ALIASES)),
            patch("services.ans.get_default_embedder") as embedder,
            patch("services.ans.default_db.hybrid_search", return_value=[]) as search,
        ):
            llm.return_value.plan_retrieval.return_value = {"question": "a dam sống bao lâu?", "queries": []}
            embedder.return_value.embed_query.return_value = [0.0]
            query, _ = _collect_hybrid_candidates("Ông ấy sống bao lâu?", history)
            llm.return_value.plan_retrieval.assert_called_once_with("Ông ấy sống bao lâu?", [
                {"role": "user", "content": "A-đam là ai?"}, history[1],
            ], ANY)
            self.assertEqual("a dam là ai?", history[0]['content'])
            self.assertEqual("A-đam sống bao lâu?", query)
            self.assertEqual(["Ông ấy sống bao lâu?", query], [c.args[0] for c in embedder.return_value.embed_query.call_args_list])
            self.assertEqual(["Ông ấy sống bao lâu?", query], [c.args[0] for c in search.call_args_list])

    def test_spelling_variants_reach_planner_and_search_identically(self):
        for canonical, variants in (
            ("Giê-su", ("Giê Su", "giê-su", "GIÊ SU", "Giesu", "Giê — Su")),
            ("Mô-sê", ("Mô Sê", "mô-sê", "MO SE")),
            ("Ma-ri-a", ("Ma Ri A", "ma-ri-a", "MARIA")),
            ("Giu-se", ("Giu Se", "giu-se", "GIUSE")),
            ("Gio-an", ("Gio An", "gio-an", "GIOAN")),
            ("Phao-lô", ("Phao Lo", "phao-lô", "PHAOLO")),
            ("Phê-rô", ("Phê Rô", "phê-rô", "PHERO")),
            ("Nô-ê", ("Nô Ê", "nô-ê", "NOE")),
            ("Sa-lô-môn", ("Sa Lô Môn", "sa-lô-môn", "SALOMON")),
        ):
            for name in variants:
                with self.subTest(name=name):
                    question = unicodedata.normalize('NFD', f'{name} là ai?')
                    history = [{'role':'user', 'content':f'Tôi đang tìm hiểu {name}.'},
                               {'role':'user', 'content':question}]
                    original_history = [dict(message) for message in history]
                    diagnostics = {}
                    with (
                        patch('services.ans.default_db.normalize_query_names', side_effect=lambda q: normalize_query_names(q, ALIASES)),
                        patch('services.ans.default_db.get_search_vocabulary', return_value=[canonical]),
                        patch('services.ans.get_llm_service') as llm,
                        patch('services.ans.get_default_embedder') as embedder,
                        patch('services.ans.default_db.hybrid_search', return_value=[]) as search,
                    ):
                        llm.return_value.plan_retrieval.side_effect = lambda q, h, v: {'question':q,'queries':[q]}
                        embedder.return_value.embed_query.return_value = [0.0]
                        query, _ = _collect_hybrid_candidates(question, history, diagnostics=diagnostics)
                        expected = f'{canonical} là ai?'
                        llm.return_value.plan_retrieval.assert_called_once_with(expected,
                            [{'role':'user','content':f'Tôi đang tìm hiểu {canonical}.'}], [canonical])
                        embedder.return_value.embed_query.assert_called_once_with(expected)
                        self.assertEqual(expected, search.call_args.args[0])
                        self.assertEqual(expected, query)
                        self.assertEqual([expected], diagnostics['search_queries'])
                        self.assertEqual(question != expected, diagnostics['query_normalized'])
                        self.assertEqual(original_history, history)

    def test_all_fixture_names_support_case_separators_and_unicode(self):
        for names in ALIASES.values():
            self.assertEqual(1, len(names))
            canonical = names[0]
            plain = ''.join(char for char in unicodedata.normalize('NFD', canonical.casefold().replace('ð','d').replace('đ','d'))
                            if unicodedata.category(char) != 'Mn')
            for variant in (canonical.upper(), canonical.lower(), canonical.replace('-', ' '),
                            canonical.replace('-', '\u00a0'), canonical.replace('-', ' — '),
                            plain.replace('-', ''), plain.replace('-', ' ')):
                with self.subTest(name=canonical, variant=variant):
                    question = unicodedata.normalize('NFD', f'{variant} là ai? Xem Ga 1:1–3.')
                    self.assertEqual(f'{canonical} là ai? Xem Ga 1:1–3.', normalize_query_names(question, ALIASES))

    def test_name_index_refreshes_when_documents_are_added_or_deleted(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "EMBEDDING_MODEL_NAME": "test/model", "EMBEDDING_DIMENSION": "3",
        }):
            db = VectorDatabase("name-index", db_path=folder)
            other = None
            try:
                db.add_vectors(["Ông Mô-sê."], [[1., 0., 0.]], [{"ingest_schema_version": 5}], ["moses"])
                db.set_collection_state("ready")
                other = VectorDatabase("name-index", db_path=folder)
                self.assertEqual("Mô-sê", other.normalize_query_names("mo se"))
                self.assertEqual("giesu", other.normalize_query_names("giesu"))
                db.add_vectors(["Đức Giê-su."], [[1., 0., 0.]], [{"ingest_schema_version": 5}], ["jesus"], rebuild_index=False)
                self.assertEqual("Giê-su", other.normalize_query_names("giesu"))
                db.delete_chunk_ids(["moses"], rebuild_index=False)
                self.assertEqual("mo se", other.normalize_query_names("mo se"))
                self.assertEqual("Giê-su", other.normalize_query_names("giesu"))
            finally:
                if other is not None:
                    other.client.close()
                db.client.close()

    def test_answer_keeps_original_question_and_source_with_normalized_name_hint(self):
        llm = object.__new__(LLMService)
        llm._generate_with_fallback = Mock(return_value="Câu trả lời.")
        context = "[Nguồn 1] Ông A-đam."
        self.assertEqual("Câu trả lời.", llm.generate_response(context, "a dam là ai?"))
        prompt = llm._generate_with_fallback.call_args.args[0]
        self.assertIn("a dam là ai?", prompt)
        self.assertIn("Câu hỏi với tên riêng đã chuẩn hóa: A-đam là ai?", prompt)
        self.assertIn(context, prompt)


if __name__ == "__main__":
    unittest.main()
