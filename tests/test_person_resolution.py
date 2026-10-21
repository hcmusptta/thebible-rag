import json
import unicodedata
import unittest
from unittest.mock import Mock, patch

from loaddocs.bible_people import mentioned_groups, passage_person_metadata, person_catalog, person_metadata
from services.person_resolution import AmbiguousPersonError, resolve_people


def assistant(report):
    return {'role': 'assistant', 'content': report.get('message', 'Đã trả lời.'), 'person_resolution': report}


class PersonResolutionTests(unittest.TestCase):
    def test_catalog_keeps_distinct_people_and_source_references(self):
        catalog = person_catalog()
        people = [person for group in catalog['groups'] for person in group['people']]
        self.assertEqual(len(people), len({person['id'] for person in people}))
        for name in ('Giuse', 'Dacaria', 'Saun'):
            group = next(group for group in catalog['groups'] if group['name'] == name)
            self.assertGreaterEqual(sum(p['testament'] == 'old' for p in group['people']), 1)
            self.assertGreaterEqual(sum(p['testament'] == 'new' for p in group['people']), 1)

    def test_name_case_spacing_hyphens_and_unicode_do_not_pick_a_person(self):
        for name in ('Giuse', 'GIU SE', 'giu-se', 'Giu — se', unicodedata.normalize('NFD', 'Giu-se')):
            with self.subTest(name=name):
                select = Mock()
                report = resolve_people(name + ' là ai?', select=select)
                self.assertEqual('clarify', report['status'])
                self.assertIn('joseph_genesis', report['options'])
                self.assertIn('joseph_arimathea', report['options'])
                select.assert_not_called()

    def test_same_testament_is_still_ambiguous(self):
        for question, expected in (
            ('Giuse trong Tân Ước là ai?', {'joseph_mary_spouse', 'joseph_arimathea'}),
            ('Dacaria trong Cựu Ước là ai?', {'zechariah_prophet', 'zechariah_jehoiada_son'}),
            ('Saun trong Cựu Ước là ai?', {'saul_israel_king', 'saul_edom_king'}),
        ):
            with self.subTest(question=question):
                report = resolve_people(question)
                self.assertEqual('clarify', report['status'])
                self.assertTrue(expected <= set(report['options']))

    def test_one_remaining_catalog_candidate_is_not_proof_of_identity(self):
        report = resolve_people('Giuse trong Cựu Ước là ai?')
        self.assertEqual('clarify', report['status'])
        self.assertIn('người khác', report['message'])

    def test_distinct_explicit_names_resolve_without_llm(self):
        for question, expected in (
            ('Gioan Tẩy Giả là ai?', 'john_baptist'),
            ('Gio-an Tẩy Giả là ai?', 'john_baptist'),
            ('GIO AN TAY GIA là ai?', 'john_baptist'),
            ('Gioan Tiền Hô là ai?', 'john_baptist'),
            ('Maria Mácđala là ai?', 'mary_magdalene'),
            ('Ma-ri-a Mác-đa-la là ai?', 'mary_magdalene'),
            ('Giuse Arimathê là ai?', 'joseph_arimathea'),
            ('Giu-se A-ri-ma-thê là ai?', 'joseph_arimathea'),
            ('Simon Phê-rô là ai?', 'simon_peter'),
        ):
            with self.subTest(question=question):
                select = Mock()
                report = resolve_people(question, select=select)
                self.assertEqual('resolved', report['status'])
                self.assertEqual([expected], report['selected_ids'])
                select.assert_not_called()

    def test_event_selection_and_invalid_ids(self):
        select = Mock(return_value={'Giuse': ['joseph_genesis']})
        report = resolve_people('Tại sao Giuse bị các anh bán?', select=select)
        self.assertEqual(['joseph_genesis'], report['selected_ids'])
        self.assertIn('Tại sao Giuse bị các anh bán?', report['question'])
        self.assertIn('Nhân vật được xác định', report['question'])
        for invalid in ('joseph_genesis', ['invented'], [{}], ['joseph_genesis', 'joseph_genesis']):
            report = resolve_people('Giuse ở Ai Cập làm gì?', select=lambda *args: {'Giuse': invalid})
            self.assertEqual('clarify', report['status'])

    def test_negation_is_not_used_as_positive_identity_evidence(self):
        self.assertEqual('clarify', resolve_people('Giuse không phải Giuse Arimathê là ai?')['status'])

    def test_unavailable_selector_requests_clarification(self):
        with self.assertLogs('services.person_resolution', level='ERROR'):
            report = resolve_people('Giuse bị các anh bán?', select=Mock(side_effect=ValueError('No provider configured')))
        self.assertEqual('clarify', report['status'])

    def test_clarification_number_resumes_original_question(self):
        pending = resolve_people('Giuse đã làm gì?')
        position = pending['options'].index('joseph_arimathea') + 1
        history = [{'role': 'user', 'content': 'Giuse đã làm gì?'}, assistant(pending)]
        original = json.dumps(history, ensure_ascii=False)
        report = resolve_people(f'Chọn {position}', history)
        self.assertEqual(['joseph_arimathea'], report['selected_ids'])
        self.assertTrue(report['question'].startswith('Giuse đã làm gì?'))
        self.assertEqual(original, json.dumps(history, ensure_ascii=False))
        self.assertEqual('clarify', resolve_people('99', history)['status'])

    def test_clarification_description_resumes_original_question(self):
        pending = resolve_people('Giuse là ai?')
        report = resolve_people('Người xin thi hài Đức Giêsu', [assistant(pending)],
                                select=lambda *args: {'Giuse': ['joseph_arimathea']})
        self.assertEqual(['joseph_arimathea'], report['selected_ids'])
        self.assertTrue(report['question'].startswith('Giuse là ai?'))

    def test_clarification_full_name_resumes_the_action_question(self):
        pending = resolve_people('Giuse đã làm gì?')
        report = resolve_people('Giuse Arimathê', [assistant(pending)])
        self.assertEqual(['joseph_arimathea'], report['selected_ids'])
        self.assertTrue(report['question'].startswith('Giuse đã làm gì?'))

    def test_number_choice_phrases_and_partial_testament_reply(self):
        pending = resolve_people('Giuse là ai?')
        position = pending['options'].index('joseph_arimathea') + 1
        for question in (str(position), f'Chọn người thứ {position}', f'Tôi chọn {position}'):
            self.assertEqual(['joseph_arimathea'], resolve_people(question, [assistant(pending)])['selected_ids'])
        report = resolve_people('Cựu Ước', [assistant(pending)])
        self.assertEqual('clarify', report['status'])
        self.assertNotIn('joseph_arimathea', report['options'])

    def test_reference_choice_identifies_person_and_preserves_original_question(self):
        pending = resolve_people('Giuse là ai?')
        report = resolve_people('Mt 27:57', [assistant(pending)])
        self.assertEqual(['joseph_arimathea'], report['selected_ids'])
        self.assertIn('Giuse là ai?', report['question'])
        self.assertIn('Mt 27:57', report['reference_question'])
        self.assertEqual('clarify', resolve_people('Mt 27', [assistant(pending)])['status'])

    def test_pronoun_and_repeated_name_keep_selected_identity(self):
        prior = resolve_people('Giuse Arimathê là ai?')
        for question in ('Ông ấy làm gì?', 'Giuse là ai?'):
            report = resolve_people(question, [assistant(prior)])
            self.assertEqual(['joseph_arimathea'], report['selected_ids'])

    def test_topic_and_explicit_person_changes_replace_previous_identity(self):
        prior = resolve_people('Giuse Arimathê là ai?')
        for question in ('Gioan là ai?', 'Thời tiết hôm nay ra sao?'):
            report = resolve_people(question, [assistant(prior)])
            self.assertNotIn('joseph_arimathea', report['selected_ids'])
        report = resolve_people('Maria Mácđala là ai?', [assistant(prior)])
        self.assertEqual(['mary_magdalene'], report['selected_ids'])

    def test_listing_all_people_is_not_forced_into_one_identity(self):
        self.assertEqual('multiple', resolve_people('Liệt kê những nhân vật tên Giuse')['status'])
        self.assertEqual('clarify', resolve_people('So sánh Giuse và Gioan')['status'])
        self.assertEqual('clarify', resolve_people('Liệt kê ba đức tính của Giuse')['status'])

    def test_book_names_and_substrings_are_not_person_mentions(self):
        for text in ('Tin Mừng Gioan là sách nào?', 'Thư Giacôbê nói gì?', 'Josephine', 'Simonton'):
            self.assertEqual([], mentioned_groups(text))

    def test_unknown_scope_does_not_select_a_person_from_another_testament(self):
        report = resolve_people('Maria trong Cựu Ước là ai?')
        self.assertEqual('clarify', report['status'])
        self.assertEqual([], report['options'])
        self.assertEqual('none', resolve_people('1', [assistant(report)])['status'])

    def test_metadata_candidates_do_not_assign_same_testament_identity(self):
        tags = person_metadata('Ông Giu-se xuất hiện.', {'testament': 'new', 'book_code': 'MAT', 'chapter': 5, 'verse_label': '1'})
        self.assertEqual([], json.loads(tags['person_ids']))
        self.assertIn('joseph_arimathea', json.loads(tags['person_candidates'])['Giuse'])
        self.assertIn('joseph_genesis', json.loads(tags['person_candidates'])['Giuse'])

    def test_old_testament_person_mentioned_in_new_testament_is_not_reidentified(self):
        tags = person_metadata('9 Các tổ phụ bán Giuse sang Ai Cập.',
                               {'testament': 'new', 'book_code': 'ACT', 'chapter': 7, 'verse_label': '9'})
        self.assertEqual(['joseph_genesis'], json.loads(tags['person_ids']))
        self.assertIn('joseph_genesis', resolve_people('Giuse trong Tân Ước là ai?')['options'])

    def test_source_anchors_and_expanded_passages_preserve_multiple_identities(self):
        verses = [
            {'text': 'Giuse, chồng bà Maria.', 'book_code': 'MAT', 'chapter': 1, 'verse_label': '16'},
            {'text': 'Giuse người Arimathê.', 'book_code': 'MAT', 'chapter': 27, 'verse_label': '57'},
        ]
        self.assertEqual(['joseph_mary_spouse'], json.loads(person_metadata(verses[0]['text'], verses[0])['person_ids']))
        self.assertEqual({'joseph_mary_spouse', 'joseph_arimathea'}, set(json.loads(passage_person_metadata(verses)['person_ids'])))


class PersonPipelineTests(unittest.TestCase):
    def test_reference_clarification_uses_selected_verse_and_original_intent(self):
        from services import ans
        from loaddocs.scripture_references import parse_references
        pending = resolve_people('Giuse là ai?')
        diagnostics = {}
        with patch.object(ans, 'ScriptureStore') as store:
            store.return_value.lookup.return_value = []
            ans.retrieve_ranked_contexts('Mt 27:57', [assistant(pending)], diagnostics=diagnostics)
        self.assertEqual(parse_references('Mt 27:57'), store.return_value.lookup.call_args.args[0])
        self.assertEqual('exact_scripture', diagnostics['retrieval_route'])
        self.assertEqual(['joseph_arimathea'], diagnostics['person_resolution']['selected_ids'])
        self.assertIn('Giuse là ai?', diagnostics['resolved_question'])

    def test_retrieval_only_benchmark_keeps_a_clarification_as_empty_context(self):
        from services import eval_ragas
        with patch.object(eval_ragas, 'retrieve_ranked_contexts', side_effect=AmbiguousPersonError('Chọn nhân vật')):
            self.assertEqual([], eval_ragas._retrieve_contexts_for_benchmark('Giuse là ai?'))

    def test_ambiguous_name_returns_clarification_before_search_or_llm(self):
        from services import ans
        diagnostics = {}
        with patch.object(ans, '_collect_hybrid_candidates') as search, patch.object(ans, 'get_llm_service') as llm:
            answer, sources = ans.answer_question('Giuse trong Tân Ước là ai?', diagnostics=diagnostics)
            self.assertIn('chồng Đức Maria', answer)
            self.assertIn('Arimathê', answer)
            self.assertEqual([], sources)
            self.assertEqual('clarification', diagnostics['answer_validation'])
            search.assert_not_called()
            llm.assert_not_called()
            with self.assertRaises(AmbiguousPersonError):
                ans.retrieve_ranked_contexts('Gioan là ai?')

    def test_search_keeps_identity_if_planner_drops_it_and_handles_old_chunks(self):
        from services import ans
        resolution = resolve_people('Giuse Arimathê là ai?')
        rows = [
            {'id': 'wrong', 'content': 'Giuse, chồng bà Maria.', 'metadata': {'book_code': 'MAT', 'chapter': 1, 'verse_label': '16'}},
            {'id': 'right', 'content': 'Giuse người Arimathê.', 'metadata': {'book_code': 'MAT', 'chapter': 27, 'verse_label': '57'}},
            {'id': 'unknown', 'content': 'Giuse xuất hiện ở đây.', 'metadata': {}},
        ]
        diagnostics = {'person_resolution': resolution}
        with (patch.object(ans, 'get_llm_service') as llm,
              patch.object(ans.default_db, 'normalize_query_names', side_effect=lambda q: q),
              patch.object(ans.default_db, 'get_search_vocabulary', return_value=[]),
              patch.object(ans.default_db, 'get_all_documents', return_value=[]),
              patch.object(ans, 'get_default_embedder') as embedder,
              patch.object(ans.default_db, 'hybrid_search', return_value=rows) as search):
            llm.return_value.plan_retrieval.return_value = {'question': 'Giuse là ai?', 'queries': ['Giuse']}
            embedder.return_value.embed_query.return_value = [0.]
            query, found = ans._collect_hybrid_candidates(resolution['question'], diagnostics=diagnostics)
        self.assertIn('Arimathê', query)
        self.assertEqual({'right', 'unknown'}, {row['id'] for row in found})
        self.assertTrue(all('Arimathê' in call.args[0] for call in search.call_args_list))

    def test_model_selection_validates_ids_group_and_verbatim_evidence(self):
        from services.model import LLMService
        llm = object.__new__(LLMService)
        group = next(group for group in person_catalog()['groups'] if group['name'] == 'Giuse')
        prompt = 'Tại sao Giuse bị các anh bán?'
        valid = {'group': 'Giuse', 'ids': ['joseph_genesis'], 'quote': 'bị các anh bán'}
        llm._generate_with_fallback = Mock(return_value=json.dumps({'selections': [valid]}))
        self.assertEqual({'Giuse': ['joseph_genesis']}, llm.select_biblical_people(prompt, [group]))
        for changes in ({'ids': ['invented']}, {'ids': ['john_baptist']}, {'quote': 'giải mộng'}, {'quote': 'Giuse'}, {'quote': ''}):
            llm._generate_with_fallback.return_value = json.dumps({'selections': [{**valid, **changes}]})
            if changes.get('ids'):
                with self.assertLogs('services.model', level='ERROR'):
                    self.assertEqual({}, llm.select_biblical_people(prompt, [group]))
            else:
                self.assertEqual({}, llm.select_biblical_people(prompt, [group]))


if __name__ == '__main__':
    unittest.main()
