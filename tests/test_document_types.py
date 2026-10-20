import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import fitz
from loaddocs.document_types import detect_document_type, document_metadata, document_type_filter, iter_prose_sections, source_details, scripture_references
from loaddocs.scripture_data import parse_scripture_pdf
from services.readdocs import process_uploaded_pdf
from services.ans import retrieve_ranked_contexts, _collect_hybrid_candidates, citations_valid, answer_question
from vectordb.vector_store import VectorDatabase


class DocumentTypeTests(unittest.TestCase):
    def pdf(self, body):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_htmlbox(fitz.Rect(30, 30, 560, 800), body, css='body {font-size:14pt}')
        return pdf

    def test_commentary_cover_overrides_scripture_filename_and_bookmark(self):
        with self.pdf('<h1>Hướng dẫn giải thích Kinh Thánh</h1><p>Chú giải Ga 1:1.</p>') as pdf:
            pdf.set_toc([[1, 'Gioan', 1]])
            self.assertEqual('commentary', detect_document_type(pdf, 'KinhThanh.pdf'))
            self.assertIsNone(parse_scripture_pdf(pdf, 'KinhThanh.pdf'))
            self.assertEqual('catechism', detect_document_type(pdf, 'KinhThanh.pdf', 'catechism'))
            with self.assertRaises(ValueError):
                detect_document_type(pdf, 'x.pdf', 'unknown')

    def test_auto_classification_handles_catechism_filenames_without_bookmarks(self):
        with self.pdf('<p>Tài liệu.</p>') as pdf:
            for source in ('toat-yeu-giao-ly-htcg.pdf','giao-ly-danh-cho-giao-ly-vien.pdf','eBookSachGiaoLyCuaHoiThanhCongGiao.pdf'):
                with self.subTest(source=source):
                    self.assertEqual('catechism', detect_document_type(pdf, source, 'auto'))
                    self.assertIsNone(parse_scripture_pdf(pdf, source, document_type='auto'))
            with self.assertRaisesRegex(ValueError, 'Tự động nhận diện'):
                parse_scripture_pdf(pdf, 'toat-yeu-giao-ly-htcg.pdf', document_type='scripture')

    def test_pdf_creator_is_not_presented_as_commentary_author(self):
        with self.pdf('<h1>Chú giải</h1><p>Nội dung.</p>') as pdf:
            pdf.set_metadata({'author':'PDF scanner operator'})
            metadata=document_metadata(pdf,'x.pdf','commentary')
            self.assertEqual('',metadata['author'])
            self.assertEqual('PDF scanner operator',metadata['pdf_author'])
            self.assertEqual('Ủy ban Kinh Thánh',document_metadata(pdf,'x.pdf','commentary',{'author':'Ủy ban Kinh Thánh'})['author'])

    def test_null_optional_metadata_matches_omitted_fields_without_changing_input(self):
        with self.pdf('<h1>Giáo lý</h1>') as pdf:
            pdf.set_metadata({'title':'Tên PDF','author':'Người tạo PDF'})
            expected = document_metadata(pdf, 'giao-ly.pdf', 'catechism')
            supplied = {key:None for key in ('title','author','translation','topic')}
            self.assertEqual(expected, document_metadata(pdf, 'giao-ly.pdf', 'catechism', supplied))
            self.assertTrue(all(value is None for value in supplied.values()))
            self.assertEqual('', expected['translation'])
            self.assertEqual('', expected['author'])

    def test_optional_metadata_keeps_type_and_length_validation(self):
        with self.pdf('<h1>Giáo lý</h1>') as pdf:
            for key in ('title','author','translation','topic'):
                for invalid in (False, 0, 123, [], {}, 'x'*501):
                    with self.subTest(key=key, invalid=invalid), self.assertRaisesRegex(ValueError, f'Metadata {key}'):
                        document_metadata(pdf, 'giao-ly.pdf', 'catechism', {key:invalid})
                self.assertEqual('x'*500, document_metadata(pdf, 'giao-ly.pdf', 'catechism', {key:'x'*500})[key])

    def test_catechism_upload_with_null_translation_commits_and_empty_repeat_is_duplicate(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'EMBEDDING_MODEL_NAME':'test/model','EMBEDDING_DIMENSION':'3'}):
            path = Path(folder) / 'giao-ly-danh-cho-giao-ly-vien.pdf'
            with self.pdf('<h1>Giáo lý</h1><p>1. Nội dung giáo lý để kiểm tra nạp tài liệu.</p>') as pdf:
                pdf.save(path)
            db = VectorDatabase('nullable-metadata', db_path=str(Path(folder)/'db'))
            embedder = Mock()
            embedder.embed_documents.side_effect = lambda texts:[[1.,0.,0.] for _ in texts]
            try:
                with patch('services.readdocs.get_default_embedder', return_value=embedder):
                    result = process_uploaded_pdf(str(path), path.name, db, document_type='catechism', metadata={'translation':None})
                    rows = db.get_all_documents()
                    self.assertEqual('catechism', result.document_type)
                    self.assertGreater(result.chunk_count, 0)
                    self.assertTrue(rows)
                    self.assertTrue(all(row['metadata']['document_type']=='catechism' and row['metadata']['translation']=='' for row in rows))
                    repeat = process_uploaded_pdf(str(path), path.name, db, document_type='catechism', metadata={'translation':''})
                    self.assertTrue(repeat.duplicate)
                    self.assertEqual(rows, db.get_all_documents())
            finally:
                db.client.close()

    def test_catechism_sections_keep_numbers_topic_and_page_boundary(self):
        with self.pdf('<h1>Giáo lý</h1><h2>Đức tin</h2><p>101. Nội dung thứ nhất.</p>') as pdf:
            page = pdf.new_page()
            page.insert_htmlbox(fitz.Rect(30,30,560,800), '<p>Phần tiếp theo của mục trước.</p><p>102. Nội dung thứ hai.</p>')
            sections=list(iter_prose_sections(pdf,'catechism'))
        first=next(s for s in sections if s['section_number']=='101')
        self.assertIn('Phần tiếp theo', first['text'])
        self.assertEqual((1,2),(first['page'],first['page_end']))
        self.assertEqual('Đức tin',first['heading'])
        self.assertTrue(any(s['section_number']=='102' for s in sections))

    def test_catechism_wrapped_question_and_numeric_cross_references_keep_the_answer(self):
        with self.pdf('<h2>4. Câu hỏi dài</h2><h2>tiếp tục cùng một đoạn?</h2><p>27 30 44</p><p>Đây là phần trả lời.</p>') as pdf:
            # Use a single bold block, matching a wrapped question in the real PDF.
            page = pdf[0]
            page.add_redact_annot(page.rect)
            page.apply_redactions()
            page.insert_htmlbox(fitz.Rect(30,70,560,400), '<p><b>4. Câu hỏi dài<br>tiếp tục cùng một đoạn?</b></p><p>27 30 44</p><p>Đây là phần trả lời.</p>')
            page.insert_text((290,780),'1',fontsize=10)
            sections = list(iter_prose_sections(pdf,'catechism'))
        asked = next(s for s in sections if s['section_number']=='4')
        self.assertIn('tiếp tục cùng một đoạn?',asked['text'])
        self.assertIn('27 30 44',asked['text'])
        self.assertIn('Đây là phần trả lời.',asked['text'])
        self.assertEqual(['4'],[s['section_number'] for s in sections if s['section_number']])

    def test_auto_catechism_ingest_preserves_sections_references_and_version(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'EMBEDDING_MODEL_NAME':'test/model','EMBEDDING_DIMENSION':'3'}):
            path = Path(folder)/'toat-yeu-giao-ly-htcg.pdf'
            with self.pdf('<h1>Giáo lý</h1><p>82. Lời giải thích với dẫn chiếu Ga 3:16.</p>') as pdf:
                pdf.save(path)
            db = VectorDatabase('auto-catechism',db_path=str(Path(folder)/'db'))
            embedder = Mock()
            embedder.embed_documents.side_effect=lambda texts:[[1.,0.,0.] for _ in texts]
            try:
                with patch('services.readdocs.get_default_embedder',return_value=embedder):
                    result=process_uploaded_pdf(str(path),path.name,db,document_type='auto',metadata={'translation':None})
                self.assertEqual('catechism',result.document_type)
                rows=db.get_all_documents()
                self.assertTrue(rows)
                numbered=[row for row in rows if row['metadata']['section_number']=='82']
                self.assertTrue(numbered)
                self.assertTrue(all(row['metadata']['prose_processing_version']==2 for row in rows))
                self.assertTrue(any('Ga 3:16' in row['metadata']['scripture_references'] for row in numbered))
                self.assertTrue(all('book_code' not in row['metadata'] for row in rows))
            finally:
                db.client.close()

    def test_ingestion_preserves_type_metadata_and_reclassification_is_not_duplicate(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'EMBEDDING_MODEL_NAME':'test/model','EMBEDDING_DIMENSION':'3'}):
            path=Path(folder)/'Huong_dan_Kinh_Thanh.pdf'
            with self.pdf('<h1>Hướng dẫn giải thích Kinh Thánh</h1><h2>Lời tựa</h2><p>Chú giải Ga 1:1 và ý nghĩa của lời văn.</p>') as pdf:
                pdf.save(path)
            db=VectorDatabase('types-test',db_path=str(Path(folder)/'db'))
            embedder=Mock();embedder.embed_documents.side_effect=lambda texts:[[1.,0.,0.] for _ in texts]
            try:
                with patch('services.readdocs.get_default_embedder',return_value=embedder):
                    first=process_uploaded_pdf(str(path),path.name,db,metadata={'author':'Tác giả A'})
                    repeat=process_uploaded_pdf(str(path),path.name,db,metadata={'author':'Tác giả A'})
                    self.assertEqual('commentary',first.document_type);self.assertTrue(repeat.duplicate)
                    docs=db.get_all_documents()
                    self.assertTrue(all(d['metadata']['document_type']=='commentary' for d in docs))
                    self.assertEqual('commentary',db.get_source_catalog()[path.name]['document_type'])
                    self.assertEqual([],db.hybrid_search('lời văn',[1.,0.,0.],filters=document_type_filter('scripture')))
                    self.assertTrue(db.hybrid_search('lời văn',[1.,0.,0.],filters=document_type_filter('commentary')))
                    self.assertTrue(all(d['metadata']['author']=='Tác giả A' for d in docs))
                    self.assertTrue(any('Ga 1:1' in d['metadata']['scripture_references'] for d in docs))
                    self.assertTrue(all('book_code' not in d['metadata'] for d in docs))
                    updated=process_uploaded_pdf(str(path),path.name,db,document_type='catechism',metadata={'topic':'Đức tin'})
                    self.assertFalse(updated.duplicate)
                    self.assertTrue(all(d['metadata']['document_type']=='catechism' for d in db.get_all_documents()))
                    self.assertTrue(all(d['metadata']['topic']=='Đức tin' for d in db.get_all_documents()))
            finally:
                db.client.close()

    def test_scripture_filter_matches_both_labels_but_not_commentary(self):
        f=document_type_filter('scripture')
        for kind in ['scripture','bible']:
            self.assertTrue(VectorDatabase._metadata_match({'document_type':kind},f))
        self.assertFalse(VectorDatabase._metadata_match({'document_type':'commentary'},f))
        self.assertIn('Loại: Bản văn Kinh Thánh',source_details({'document_type':'bible'}))

    def test_commentary_reference_uses_semantic_search_not_exact_scripture(self):
        with patch('services.ans.ScriptureStore.lookup') as exact, patch('services.ans._collect_hybrid_candidates',return_value=('Ga 1:1',[])) as semantic:
            self.assertEqual([],retrieve_ranked_contexts('Chú giải Ga 1:1',document_type='commentary'))
        exact.assert_not_called()
        self.assertIn({'document_type':'commentary'}, semantic.call_args.kwargs['filters']['$and'])

    def test_all_sources_doctrine_question_with_bible_reference_keeps_semantic_search(self):
        for question in ('Theo giáo lý, Ga 3:16 có ý nghĩa gì?', 'Chú giải Ga 3:16 nói gì?'):
            with self.subTest(question=question), patch('services.ans.ScriptureStore.lookup') as exact, patch('services.ans._collect_hybrid_candidates',return_value=(question,[])) as semantic:
                self.assertEqual([],retrieve_ranked_contexts(question,document_type='all',edition_id='chosen'))
                exact.assert_not_called()
                filters=semantic.call_args.kwargs['filters']
                self.assertTrue(VectorDatabase._metadata_match({'document_type':'catechism','edition_id':'chosen'},filters))
                self.assertTrue(VectorDatabase._metadata_match({'document_type':'scripture','edition_id':'chosen','processing_version':4},filters))
                self.assertFalse(VectorDatabase._metadata_match({'document_type':'catechism','edition_id':'other'},filters))

    def test_doctrine_reference_can_include_original_bible_without_replacing_prose(self):
        prose={'id':'prose','content':'Giáo lý giải thích.', 'metadata':{'document_type':'catechism','edition_id':'chosen'}}
        bible={'id':'bible','content':'16 Bản văn Kinh Thánh.', 'metadata':{'document_type':'scripture','edition_id':'chosen'}}
        with patch('services.ans._collect_hybrid_candidates',return_value=('Theo giáo lý, Ga 3:16 có ý nghĩa gì?',[prose])), patch('services.ans._rerank_with_cross_encoder',return_value=[prose]), patch('services.ans.ScriptureStore.lookup',return_value=[bible]) as lookup:
            diagnostics={}
            results=retrieve_ranked_contexts('Theo giáo lý, Ga 3:16 có ý nghĩa gì?',document_type='all',edition_id='chosen',diagnostics=diagnostics)
            self.assertEqual(['catechism','scripture'],[row['metadata']['document_type'] for row in results])
            self.assertEqual([row['content'] for row in results],diagnostics['final_contexts'])
            self.assertEqual(('chosen',None),lookup.call_args.args[1:])
            lookup.reset_mock()
            results=retrieve_ranked_contexts('Theo giáo lý, Ga 3:16 có ý nghĩa gì?',document_type='catechism',edition_id='chosen')
            lookup.assert_not_called()

    def test_explicit_scripture_scope_still_uses_exact_lookup_for_doctrine_wording(self):
        with patch('services.ans.ScriptureStore.lookup',return_value=[]) as exact, patch('services.ans._collect_hybrid_candidates') as semantic:
            self.assertEqual([],retrieve_ranked_contexts('Giáo lý nói gì? Đọc Ga 3:16.',document_type='scripture'))
            exact.assert_called_once()
            semantic.assert_not_called()

    def test_concept_counts_and_finite_lists_do_not_trigger_literal_bible_counts(self):
        for question in ('Có bao nhiêu bí tích?', 'Liệt kê tất cả bảy bí tích.', 'Có bao nhiêu Tông Đồ?'):
            with self.subTest(question=question), patch('services.ans.ScriptureStore.exhaustive') as exhaustive, patch('services.ans._collect_hybrid_candidates',return_value=(question,[])) as semantic:
                self.assertEqual([],retrieve_ranked_contexts(question,document_type='all'))
                exhaustive.assert_not_called()
                semantic.assert_called_once()

    def test_literal_counts_and_all_verse_requests_keep_exhaustive_routing(self):
        for question in ('Đếm "thương xót"', 'Có bao nhiêu lần "thương xót"?', 'Tất cả "thương xót"', 'Tìm tất cả các câu có cụm từ này'):
            with self.subTest(question=question), patch('services.ans.ScriptureStore.exhaustive',return_value=[]) as exhaustive, patch('services.ans._collect_hybrid_candidates') as semantic:
                self.assertEqual([],retrieve_ranked_contexts(question,document_type='scripture'))
                exhaustive.assert_called_once()
                semantic.assert_not_called()

    def test_catechism_references_validate_only_the_cited_coordinates(self):
        rows=[{'content':'82. Lời giải thích có dẫn chiếu Ga 3:16.', 'metadata':{'document_type':'catechism','section_number':'82','scripture_references':'Ga 3:16'}}]
        self.assertTrue(citations_valid('Tài liệu giáo lý dẫn chiếu Ga 3:16. [Nguồn 1]',rows))
        self.assertFalse(citations_valid('Tài liệu giáo lý dẫn chiếu Ga 3:17. [Nguồn 1]',rows))

    def test_invalid_prose_reference_does_not_abort_extraction_or_reverse_the_range(self):
        from loaddocs.scripture_references import parse_references
        text='Dẫn chiếu Ga 3:16-15; Rm 0:1. Đối chiếu Ga 3:16.'
        refs=scripture_references(text)
        self.assertEqual(['JHN 3:16'],[ref.display() for ref in parse_references(refs)])
        self.assertIn('Ga 3:16-15',text)

    def test_catechism_and_mixed_sources_use_grounding_without_unchecked_fallback(self):
        catechism={'content':'82. Lời giải thích.', 'metadata':{'document_type':'catechism','section_number':'82'}}
        scripture={'content':'16 Bản văn nguồn.', 'metadata':{'document_type':'scripture'}}
        for rows in ([catechism],[catechism,scripture]):
            with self.subTest(rows=rows), patch('services.ans.retrieve_ranked_contexts',return_value=rows), patch('services.ans.get_llm_service') as llm:
                llm.return_value.generate_scripture_response.return_value={'answer':'Theo giáo lý, lời giải thích. [Nguồn 1]','claims':[],'clarify':False}
                diagnostics={}
                answer,_=answer_question('Theo giáo lý, ý này là gì?',diagnostics=diagnostics)
                self.assertIn('Theo giáo lý',answer)
                self.assertEqual('citation_and_quote_checked',diagnostics['answer_validation'])
                llm.return_value.generate_response.assert_not_called()
                llm.return_value.generate_scripture_response.return_value=None
                diagnostics={}
                answer,_=answer_question('Theo giáo lý, ý này là gì?',diagnostics=diagnostics)
                self.assertEqual('grounding_rejected',diagnostics['answer_validation'])
                llm.return_value.generate_response.assert_not_called()

    def test_matching_source_questions_keep_scope_and_outrank_related_subtopics(self):
        from services import ans
        definition={'id':'definition','content':'224. Các X là gì? X là định nghĩa nguồn.',
                    'metadata':{'document_type':'catechism','edition_id':'chosen','parent_id':'definition-parent'}}
        duplicate={**definition,'id':'same-parent'}
        subtopic={'id':'subtopic','content':'231. Thuộc tính của X là gì? Đây là thuộc tính.',
                  'metadata':{'document_type':'catechism','edition_id':'chosen'}}
        other={**definition,'id':'other-edition','metadata':{**definition['metadata'],'edition_id':'other','parent_id':'other'}}
        scope={'edition_id':'chosen'}
        with patch.object(ans.default_db,'get_all_documents',return_value=[subtopic,definition,duplicate,other]):
            matches=ans._matching_prose_sources('Theo giáo lý, X là gì?',scope)
            self.assertEqual(['definition'],[row['id'] for row in matches])
            self.assertEqual([],ans._matching_prose_sources('X có thuộc tính gì?',scope))
        ranker=Mock()
        ranker.predict.return_value=[0.99,0.01]
        ranker.max_length=4096
        ranker.tokenizer.encode.side_effect=lambda text,**kwargs: list(text)
        ranker.tokenizer.num_special_tokens_to_add.return_value=3
        with patch.object(ans,'_get_reranker',return_value=ranker),patch.dict('os.environ',{'RERANK_ENABLED':'true'}):
            ranked=ans._rerank_with_cross_encoder('X là gì?',[subtopic,*matches])
        self.assertEqual('definition',ranked[0]['id'])

    def test_prose_list_requires_independent_review_and_preserves_source_details(self):
        from services.model import LLMService
        llm=object.__new__(LLMService)
        facts={'clarify':False,'claims':[{'text':'X kiên nhẫn.','evidence':['S1E1']},
                                        {'text':'X nhân hậu.','evidence':['S1E1']}]}
        llm._generate_with_fallback=Mock(side_effect=[json.dumps(facts),'{"supported":[1,2],"sufficient":true,"unsupported_details":[]}'])
        sources=[{'content':'2. X kiên nhẫn, X nhân hậu.', 'metadata':{'document_type':'catechism','section_number':'2'}}]
        result=llm.generate_scripture_response('Nêu hai đặc điểm của X.',sources,resolved_question='Đặc điểm X')
        self.assertEqual(2,len(result['claims']))
        self.assertEqual(2,llm._generate_with_fallback.call_count)
        schema=llm._generate_with_fallback.call_args_list[0].kwargs['response_schema']
        self.assertIn('claims',schema['properties'])
        original_payload=json.loads(llm._generate_with_fallback.call_args_list[0].args[0])
        self.assertEqual('Nêu hai đặc điểm của X.',original_payload['question'])
        self.assertEqual('Đặc điểm X',original_payload['resolved_question'])
        review=json.loads(llm._generate_with_fallback.call_args.args[0])
        self.assertEqual('Nêu hai đặc điểm của X.',review['question'])
        self.assertEqual('Đặc điểm X',review['resolved_question'])
        details=review['claims'][0]['paragraphs'][0]['details']
        self.assertIn('Loại: Giáo lý',details)
        self.assertIn('Số mục: 2',details)

    def test_known_source_question_returns_original_answer_without_model_paraphrase(self):
        row={'content':'224. Các X là gì?\n\n1113-1131\n\nX là định nghĩa nguồn.',
             'metadata':{'document_type':'catechism'},'source_question_match':True}
        with patch('services.ans.retrieve_ranked_contexts',return_value=[row]),patch('services.ans.get_llm_service') as llm:
            diagnostics={}
            answer,rows=answer_question('Theo giáo lý, X là gì?',diagnostics=diagnostics)
            self.assertIn('X là định nghĩa nguồn.',answer)
            self.assertIn('[Nguồn 1]',answer)
            self.assertEqual('source_qa_quote_checked',diagnostics['answer_validation'])
            self.assertEqual([row['content']],diagnostics['grounding_evidence'][0]['quotes'])
            llm.assert_not_called()

    def test_mixed_source_answer_can_separate_doctrine_from_bible_claim(self):
        from services.model import LLMService
        llm=object.__new__(LLMService)
        facts={'clarify':False,'claims':[{'text':'Theo giáo lý, X là khái niệm này.','evidence':['S1E1']},
                                        {'text':'Bản văn Kinh Thánh nói Y thực hiện hành động.','evidence':['S2E1']}]}
        llm._generate_with_fallback=Mock(side_effect=[json.dumps(facts),'{"supported":[1,2],"sufficient":true,"unsupported_details":[]}'])
        rows=[{'content':'X là khái niệm này.','metadata':{'document_type':'catechism'}},
              {'content':'1 Y thực hiện hành động.','metadata':{'document_type':'scripture'}}]
        result=llm.generate_scripture_response('So sánh giáo lý và Kinh Thánh nói gì về ý này?',rows)
        self.assertEqual(2,len(result['claims']))
        self.assertEqual([[1],[2]],[claim['sources'] for claim in result['claims']])

    def test_explicit_doctrine_request_does_not_use_bible_context_as_doctrine_proof(self):
        from services.model import LLMService
        llm=object.__new__(LLMService)
        facts={'clarify':False,'claims':[{'text':'Theo giáo lý, X là khái niệm này.','evidence':['S1E1']}]}
        llm._generate_with_fallback=Mock(side_effect=[json.dumps(facts),'{"supported":[1],"sufficient":true,"unsupported_details":[]}'])
        rows=[{'content':'X là khái niệm này.','metadata':{'document_type':'catechism'}},
              {'content':'1 Y thực hiện hành động.','metadata':{'document_type':'scripture'}}]
        result=llm.generate_scripture_response('Theo giáo lý, X là gì?',rows)
        self.assertEqual([1],result['claims'][0]['sources'])
        schema=llm._generate_with_fallback.call_args_list[0].kwargs['response_schema']
        self.assertEqual(['S1E1'],schema['properties']['claims']['items']['properties']['evidence']['items']['enum'])
        payload=json.loads(llm._generate_with_fallback.call_args_list[0].args[0])
        self.assertFalse(payload['sources'][1]['evidence_allowed'])

    def test_matching_source_question_is_kept_when_hybrid_search_misses_it(self):
        from services import ans
        row={'id':'definition','content':'224. Các X là gì? X là định nghĩa nguồn.',
             'metadata':{'document_type':'catechism','edition_id':'chosen'}}
        llm=Mock()
        llm.plan_retrieval.return_value={'question':'X là gì?','queries':[]}
        with patch.object(ans.default_db,'get_all_documents',return_value=[row]), patch.object(ans.default_db,'normalize_query_names',side_effect=lambda q:q), patch.object(ans,'get_llm_service',return_value=llm), patch.object(ans,'get_default_embedder') as embedder, patch.object(ans.default_db,'hybrid_search',return_value=[]):
            embedder.return_value.embed_query.return_value=[0.0]
            _,rows=ans._collect_hybrid_candidates('Theo giáo lý, X là gì?',filters={'edition_id':'chosen'})
            self.assertEqual(['definition'],[item['id'] for item in rows])

    def test_prose_cross_references_match_ranges_without_escaping_edition_scope(self):
        from services import ans
        good={'id':'good','content':'Lời giải thích trong giáo lý.',
              'metadata':{'document_type':'catechism','edition_id':'chosen','scripture_references':'Ga 3:16-17'}}
        unrelated={**good,'id':'unrelated','metadata':{**good['metadata'],'scripture_references':'Ga 3:17'}}
        other={**good,'id':'other','metadata':{**good['metadata'],'edition_id':'other'}}
        with patch.object(ans.default_db,'get_all_documents',return_value=[unrelated,good,other]):
            matches=ans._matching_prose_sources('Theo giáo lý, Ga 3:16 liên quan đến điều gì?',{'edition_id':'chosen'})
            self.assertEqual(['good'],[row['id'] for row in matches])
            self.assertTrue(matches[0]['source_reference_match'])
            self.assertFalse(matches[0]['source_question_match'])

    def test_fallback_cannot_escape_document_type(self):
        embedder=Mock();embedder.embed_query.return_value=[1.,0.,0.]
        with patch('services.ans.get_llm_service'), patch.dict('os.environ',{'RAG_FILTER_FALLBACK':'true'}), patch('services.ans.get_default_embedder',return_value=embedder), patch('services.ans.default_db.hybrid_search',return_value=[]) as search:
            _collect_hybrid_candidates('test',filters={'document_type':'catechism','topic':'Đức tin'})
        self.assertEqual(1,search.call_count)
        self.assertEqual({'document_type':'catechism','topic':'Đức tin'},search.call_args_list[0].kwargs['filters'])

if __name__ == '__main__':
    unittest.main()
