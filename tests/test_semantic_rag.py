import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from services import ans
from services.model import LLMService, _OllamaProvider, ContextBudgetError
from vectordb.vector_store import VectorDatabase
from loaddocs.scripture_store import ScriptureStore
from loaddocs.scripture_data import ScriptureData


class SemanticRagTests(unittest.TestCase):
    def test_lexical_search_and_evidence_selection_use_verified_parent_context(self):
        child = '16 X làm việc được hỏi.'
        parent = '12 Y xuất hiện.\n' + child
        row = {'id':'right','content':child,'metadata':{'parent_content':parent,'parent_id':'paragraph','document_type':'scripture'}}
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ',{'EMBEDDING_MODEL_NAME':'test/model','EMBEDDING_DIMENSION':'3'}):
            db = VectorDatabase('parent-context',db_path=folder)
            try:
                db.add_vectors([child,'Nội dung khác','Văn bản thứ ba'], [[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]],
                    [{'ingest_schema_version':5,'parent_content':parent},
                     {'ingest_schema_version':5,'parent_content':'Y trong đoạn không chứa câu con này'},
                     {'ingest_schema_version':5}], ['right','wrong','other'])
                db.set_collection_state('ready')
                found = db.search_by_bm25('Y')
                self.assertEqual(['right'], [item['id'] for item in found])
                self.assertEqual(child, found[0]['content'])
            finally:
                db.client.close()
        llm = object.__new__(LLMService)
        llm._generate_with_fallback = Mock(return_value='{"ids":[1]}')
        selected = llm.select_scripture_evidence('Ai làm việc đó với Y?', [row], 4, [])
        self.assertEqual([row], selected)
        payload = json.loads(llm._generate_with_fallback.call_args.args[0])
        self.assertEqual(parent, payload['sources'][0]['text'])
        self.assertEqual(child, selected[0]['content'])

    def test_source_selection_deduplicates_parent_previews_within_same_edition(self):
        parent = '1 X làm việc.\n2 Y nhận kết quả.'
        row = {'id':'first','content':'1 X làm việc.','metadata':{'parent_id':'p','parent_content':parent,'edition_id':'a'}}
        second = {'id':'second','content':'2 Y nhận kết quả.','metadata':row['metadata']}
        other = {**row,'id':'other','metadata':{**row['metadata'],'edition_id':'b'}}
        llm = object.__new__(LLMService)
        llm._generate_with_fallback = Mock(return_value='{"ids":[1,2]}')
        selected = llm.select_scripture_evidence('X làm việc gì?', [row,second,other], 4, [])
        self.assertEqual(['first','other'], [item['id'] for item in selected])
        payload = json.loads(llm._generate_with_fallback.call_args.args[0])
        self.assertEqual(2, len(payload['sources']))

    def test_inverse_identity_adds_action_probe_without_changing_scope_or_question(self):
        llm = Mock(); llm.plan_retrieval.return_value = {'question':'Ai hướng dẫn ông X-a?', 'queries':[]}
        scope = {'edition_id':'selected'}
        diagnostics = {}
        with patch.object(ans,'get_llm_service',return_value=llm), \
             patch.object(ans.default_db,'normalize_query_names',side_effect=lambda q:q), \
             patch.object(ans.default_db,'get_all_documents',return_value=[]), \
             patch.object(ans,'get_default_embedder') as embedder, \
             patch.object(ans.default_db,'hybrid_search',return_value=[]) as search:
            embedder.return_value.embed_query.return_value = [0.0]
            ans._collect_hybrid_candidates('Ai hướng dẫn ông X-a?',filters=scope,diagnostics=diagnostics)
        self.assertEqual(['Ai hướng dẫn ông X-a?','hướng dẫn'],diagnostics['search_queries'])
        self.assertTrue(all(call.kwargs['filters']==scope for call in search.call_args_list))
        self.assertEqual('Ai hướng dẫn ông X-a?',diagnostics['retrieval_query'])

    def test_simple_identity_with_mixed_sources_keeps_one_direct_claim(self):
        llm = object.__new__(LLMService)
        rows = [{'content':'1 Người thực hiện việc ấy là X.', 'metadata':{'document_type':'scripture'}},
                {'content':'2. X còn làm việc khác.', 'metadata':{'document_type':'catechism'}}]
        llm._generate_with_fallback = Mock(side_effect=[
            '{"clarify":false,"claims":[{"text":"Người thực hiện việc ấy là X.","evidence":["S1E1"]}]}',
            '{"supported":[1],"sufficient":true,"unsupported_details":[]}'])
        result = llm.generate_scripture_response('Ai thực hiện việc ấy?', rows)
        self.assertIn('là X', result['answer'])
        self.assertEqual(3, llm._generate_with_fallback.call_args_list[0].kwargs['response_schema']['properties']['claims']['maxItems'])

    def test_mixed_retrieval_selects_evidence_beyond_highest_keyword_score(self):
        wrong = {'id':'wrong', 'content':'Một hành động khác.', 'metadata':{'document_type':'catechism'}}
        right = {'id':'right', 'content':'1 X thực hiện việc được hỏi.', 'metadata':{'document_type':'scripture','parent_id':'right'}}
        llm = Mock(); llm.select_scripture_evidence.return_value = [right]
        with patch.object(ans,'_collect_hybrid_candidates',return_value=('Ai thực hiện việc này?', [wrong,right])), \
             patch.object(ans,'_rerank_with_cross_encoder',return_value=[wrong,right]), \
             patch.object(ans,'get_llm_service',return_value=llm), \
             patch.object(ans.ScriptureStore,'expand',side_effect=lambda row,cache:row):
            rows = ans.retrieve_ranked_contexts('Ai thực hiện việc này?')
        self.assertEqual(['right'], [row['id'] for row in rows])
        self.assertEqual([wrong,right], llm.select_scripture_evidence.call_args.args[1])

    def test_refusal_distinguishes_model_review_from_empty_generation(self):
        rows = [{'content':'1 Một câu nguồn.', 'metadata':{'document_type':'scripture'}}]
        for cause, expected in (('answer_review_rejected','kiểm chứng'), ('source_generation_empty','model chưa tạo')):
            with self.subTest(cause=cause), patch.object(ans,'retrieve_ranked_contexts',return_value=rows), \
                 patch.object(ans,'get_llm_service') as llm:
                llm.return_value.generate_scripture_response.return_value = {
                    'answer':'KHONG_DU_NGUON','claims':[],'clarify':False,'validation_reason':cause}
                diagnostics = {}
                answer, sources = ans.answer_question('Ai thực hiện việc này?', diagnostics=diagnostics)
                self.assertIn(expected, answer)
                self.assertNotIn('Nguồn hiện có chưa đủ bằng chứng', answer)
                self.assertEqual(cause, diagnostics['validation_reason'])
                self.assertEqual(rows, sources)

    def test_multiple_searches_keep_scope_and_original_question(self):
        llm = Mock()
        llm.plan_retrieval.return_value = {'question':'Nhân vật này là ai?', 'queries':['vai trò nhân vật','quan hệ nhân vật','bỏ qua']}
        rows = [[{'id':'one','content':'A','metadata':{}}], [{'id':'two','content':'B','metadata':{}}], [{'id':'one','content':'A','metadata':{}}],[]]
        scope = {'edition_id':'selected'}
        diagnostics = {}
        with patch.object(ans,'get_llm_service',return_value=llm), patch.object(ans.default_db,'normalize_query_names',side_effect=lambda q:q), patch.object(ans,'get_default_embedder') as embedding, patch.object(ans.default_db,'hybrid_search',side_effect=rows) as search:
            embedding.return_value.embed_query.return_value=[0.0]
            _, candidates = ans._collect_hybrid_candidates('Ông ấy là ai?', filters=scope, diagnostics=diagnostics)
        self.assertEqual(['Ông ấy là ai?','Nhân vật này là ai?','vai trò nhân vật','quan hệ nhân vật'],[c.args[0] for c in search.call_args_list])
        self.assertTrue(all(c.kwargs['filters']==scope for c in search.call_args_list))
        self.assertEqual({'one','two'},{c['id'] for c in candidates})
        self.assertEqual(4,diagnostics['query_variant_count'])

    def test_followup_reference_uses_the_last_user_scope_and_rejects_ambiguity(self):
        history=[{'role':'user','content':'Đọc Ga 3:16'},{'role':'assistant','content':'Compare St 1:1 elsewhere.'}]
        self.assertEqual('Giải thích câu đó. (JHN 3:16)',ans._resolve_scripture_followup('Giải thích câu đó.',history))
        self.assertEqual('Giải thích câu đó. (JHN 3:16)',ans._resolve_scripture_followup('Giải thích câu đó.',history+[{'role':'user','content':'Giải thích câu đó.'}]))
        self.assertEqual('Đọc St 1:1',ans._resolve_scripture_followup('Đọc St 1:1',history))
        history.append({'role':'user','content':'Mô-sê là ai?'})
        self.assertEqual('Giải thích câu đó.',ans._resolve_scripture_followup('Giải thích câu đó.',history))
        for prior in ('Đọc Ga 3:16 và St 1:1','Đọc Ga 3:16-18','Đọc Ga 3'):
            with self.subTest(prior=prior),self.assertRaises(ans.IncompleteScriptureReference):
                ans._resolve_scripture_followup('Giải thích câu đó.',[{'role':'user','content':prior}])
        self.assertIn('JHN 3:16-18',ans._resolve_scripture_followup('Giải thích đoạn đó.',[{'role':'user','content':'Đọc Ga 3:16-18'}]))

    def test_unqualified_names_preserve_alternate_probe_sources_before_ambiguity_check(self):
        rows=[{'id':str(i),'content':f'Nguồn người {i}', 'name_variant_leader':True,
               'metadata':{'document_type':'scripture','parent_id':str(i)}} for i in range(2)]
        llm=Mock(); llm.select_scripture_evidence.return_value=rows[:1]
        with patch.object(ans,'_collect_hybrid_candidates',return_value=('X-a là ai?',rows)),patch.object(ans,'get_llm_service',return_value=llm),patch.object(ans.ScriptureStore,'expand',side_effect=lambda row,cache:row):
            result=ans.retrieve_ranked_contexts('X-a là ai?',top_k=4)
        self.assertEqual(2,len(result))
        with patch.object(ans,'_collect_hybrid_candidates',return_value=('Vợ X-a là ai?',rows)),patch.object(ans,'get_llm_service',return_value=llm),patch.object(ans.ScriptureStore,'expand',side_effect=lambda row,cache:row):
            result=ans.retrieve_ranked_contexts('Vợ X-a là ai?',top_k=4)
        self.assertEqual(1,len(result))

    def test_followup_exact_lookup_preserves_edition_and_passes_resolved_question_to_answering(self):
        row={'content':'16 Nguyên văn nguồn.','metadata':{'source':'source.pdf','reference':'JHN 3:16','document_type':'scripture','passage_id':'p','last_passage_id':'p'}}
        llm=Mock(); llm.generate_scripture_response.return_value={'answer':'Ý nghĩa. [Nguồn 1]','claims':[],'clarify':False}
        with patch.object(ans.ScriptureStore,'lookup',return_value=[row]) as lookup,patch.object(ans.ScriptureStore,'expand',return_value=row),patch.object(ans,'get_llm_service',return_value=llm):
            answer,_=ans.answer_question('Giải thích câu đó.',chat_history=[{'role':'user','content':'Đọc Ga 3:16'}],edition_id='chosen')
        self.assertEqual('JHN 3:16',lookup.call_args.args[0][0].display())
        self.assertEqual('chosen',lookup.call_args.args[1])
        self.assertIn('JHN 3:16',llm.generate_scripture_response.call_args.args[0])
        self.assertIn('Ý nghĩa',answer)

    def test_question_with_a_reference_gets_an_answer_but_read_requests_keep_verbatim(self):
        row={'content':'1 Nguyên văn nguồn.','metadata':{'source':'source.pdf','reference':'GEN 1:1','document_type':'scripture','passage_id':'p','last_passage_id':'p'}}
        llm=Mock(); llm.generate_scripture_response.return_value={'answer':'Chủ thể trong nguồn. [Nguồn 1]','claims':[],'clarify':False}
        with patch.object(ans,'retrieve_ranked_contexts',return_value=[row]),patch.object(ans.ScriptureStore,'expand',return_value=row),patch.object(ans,'get_llm_service',return_value=llm):
            answer,_=ans.answer_question('Trong St 1:1, ai thực hiện việc này?',diagnostics={'retrieval_route':'exact_scripture'})
            self.assertIn('Chủ thể',answer)
            llm.generate_scripture_response.reset_mock()
            answer,_=ans.answer_question('Đọc St 1:1',diagnostics={'retrieval_route':'exact_scripture'})
            self.assertIn('Nguyên văn nguồn',answer)
            llm.generate_scripture_response.assert_not_called()

    def test_source_selector_receives_the_resolved_subject_in_followup_questions(self):
        item={'id':'p','content':'Tên X có nhiệm vụ.','metadata':{'document_type':'scripture'}}
        llm=Mock(); llm.select_scripture_evidence.return_value=[item]
        with patch.object(ans,'_collect_hybrid_candidates',return_value=('X đã làm gì?', [item])),patch.object(ans,'get_llm_service',return_value=llm),patch.object(ans.ScriptureStore,'expand',return_value=item):
            ans.retrieve_ranked_contexts('Vậy ông ấy làm gì?')
        self.assertEqual('X đã làm gì?',llm.select_scripture_evidence.call_args.args[0])

    def test_letter_opening_provides_sender_context_from_the_same_edition(self):
        row={'content':'7 Tôi được giao nhiệm vụ.','metadata':{'source':'source.pdf','reference':'GAL 2:7','document_type':'scripture','passage_id':'p','last_passage_id':'p','book_code':'GAL','edition_id':'chosen'}}
        opening={'content':'1 Phao-lô, tông đồ.\n2 Kính gửi Hội Thánh.','metadata':{'source':'source.pdf','reference':'GAL 1:1-3','document_type':'scripture','edition_id':'chosen'}}
        llm=Mock(); llm.generate_scripture_response.return_value={'answer':'Đúng chủ thể. [Nguồn 1]','claims':[],'clarify':False}
        with patch.object(ans,'retrieve_ranked_contexts',return_value=[row]),patch.object(ans.ScriptureStore,'expand',return_value=row),patch.object(ans.ScriptureStore,'lookup',return_value=[opening]) as lookup,patch.object(ans,'get_llm_service',return_value=llm):
            ans.answer_question('Trong Gl 2:7, ai nói điều này?',diagnostics={'retrieval_route':'exact_scripture'})
        self.assertEqual('chosen',lookup.call_args.args[1])
        self.assertEqual('GAL 1:1-3',lookup.call_args.args[0][0].display())
        self.assertIn(opening,llm.generate_scripture_response.call_args.args[1])

    def test_optional_exact_context_can_be_removed_without_losing_requested_verses(self):
        row={'content':'7 Bản văn yêu cầu.','metadata':{'source':'source.pdf','reference':'GEN 1:7','document_type':'scripture','passage_id':'p','last_passage_id':'p'}}
        neighbor={**row,'content':'6 Văn mạch.\n7 Bản văn yêu cầu.'}
        llm=Mock(); llm.generate_scripture_response.side_effect=[ContextBudgetError('budget'),{'answer':'Diễn giải. [Nguồn 1]','claims':[],'clarify':False}]
        with patch.object(ans,'retrieve_ranked_contexts',return_value=[row]),patch.object(ans.ScriptureStore,'expand',return_value=neighbor),patch.object(ans,'get_llm_service',return_value=llm):
            answer,kept=ans.answer_question('Giải thích St 1:7',diagnostics={'retrieval_route':'exact_scripture'})
        self.assertEqual([row],kept)
        self.assertIn('Bản văn yêu cầu',answer)
        self.assertEqual([row],llm.generate_scripture_response.call_args.args[1])

    def test_failed_planning_keeps_search_available_and_filtered(self):
        llm=Mock(); llm.plan_retrieval.side_effect=RuntimeError('model unavailable')
        with patch.object(ans,'get_llm_service',return_value=llm), patch.object(ans.default_db,'normalize_query_names',side_effect=lambda q:q), patch.object(ans,'get_default_embedder') as embedding, patch.object(ans.default_db,'hybrid_search',return_value=[]) as search:
            embedding.return_value.embed_query.return_value=[0.0]
            ans._collect_hybrid_candidates('original',filters={'edition_id':'one'})
        search.assert_called_once_with('original',[0.0],k=15,filters={'edition_id':'one'})

    def test_overlap_does_not_discard_the_best_hit_of_a_supplementary_query(self):
        llm=Mock(); llm.plan_retrieval.return_value={'question':'original','queries':['role','event']}
        shared=[{'id':f'common{i}','content':'Name mention','metadata':{}} for i in range(10)]
        focused={'id':'role-evidence','content':'Explicit role','metadata':{}}
        with patch.object(ans,'get_llm_service',return_value=llm), patch.object(ans.default_db,'normalize_query_names',side_effect=lambda q:q), patch.object(ans,'get_default_embedder') as embedding, patch.object(ans.default_db,'hybrid_search',side_effect=[shared,[focused,*shared],shared]):
            embedding.return_value.embed_query.return_value=[0.0]
            _,candidates=ans._collect_hybrid_candidates('original',candidate_limit=8)
        self.assertIn('role-evidence',[item['id'] for item in candidates])
        self.assertEqual(8,len(candidates))

    def test_planner_rejects_malformed_or_unbounded_output(self):
        llm=object.__new__(LLMService)
        for result in ('not json', '[]', '{"question":3}', json.dumps({'question':'q','queries':['x'*401]}), json.dumps({'question':'q','queries':'text'})):
            with self.subTest(result=result):
                llm._generate_with_fallback=Mock(return_value=result)
                self.assertEqual({'question':'original','queries':[]},llm.plan_retrieval('original'))
        llm._generate_with_fallback=Mock(return_value='```json\n{"question":"standalone","queries":["a","a","b","c"]}\n```')
        self.assertEqual({'question':'standalone','queries':['a','b']},llm.plan_retrieval('original'))
        self.assertIn('original',llm._generate_with_fallback.call_args.args[0])
        self.assertNotIn('original',llm._generate_with_fallback.call_args.kwargs['system_prompt'])

    def test_ranking_preserves_matched_verse_and_its_multiline_context(self):
        child='Tên nhân vật là X.'
        parent='Người kể giới thiệu.\nTên nhân vật\nlà X.\nX được giao một nhiệm vụ.'
        item={'id':'x','content':child,'metadata':{'parent_content':parent}}
        self.assertEqual(parent,ans._select_context_content(item))
        ranker=Mock(); ranker.predict.return_value=[0.7]
        ranker.max_length=4096
        ranker.tokenizer.encode.side_effect=lambda text,**kwargs: list(text)
        ranker.tokenizer.num_special_tokens_to_add.return_value=3
        with patch.object(ans,'_get_reranker',return_value=ranker), patch.dict('os.environ',{'RERANK_ENABLED':'true'}):
            ans._rerank_with_cross_encoder('X là ai?',[item])
        text=ranker.predict.call_args.args[0][0][1]
        self.assertTrue(text.startswith(child))
        self.assertIn('nhiệm vụ',text)

    def test_unknown_annotation_does_not_hide_explicit_source_facts(self):
        row={'content':'Bà X là mẹ của Y.','metadata':{'annotations':json.dumps({'REF':{'speaker':None,'origin':'unknown'}})}}
        formatted=ans._format_context([row])
        self.assertIn('Bà X là mẹ của Y.',formatted)
        self.assertNotIn('speaker',formatted)
        row['metadata']['annotations']=json.dumps({'REF':{'speaker':'X','origin':'reviewed','source_locator':'page 1'}})
        self.assertIn('page 1',ans._format_context([row]))

    def test_insufficient_evidence_is_not_reported_as_broken_citations(self):
        llm=Mock(); llm.generate_scripture_response.return_value={'answer':'KHONG_DU_NGUON','claims':[],'clarify':False}
        diagnostics={}
        with patch.object(ans,'retrieve_ranked_contexts',return_value=[{'content':'Unrelated event','metadata':{}}]), patch.object(ans,'get_llm_service',return_value=llm):
            answer,_=ans.answer_question('Unknown birth date?',diagnostics=diagnostics)
        self.assertIn('chưa đủ bằng chứng',answer)
        self.assertEqual('insufficient_evidence',diagnostics['answer_validation'])

    def test_evidence_selector_cannot_invent_source_ids_or_duplicate_paragraphs(self):
        llm=object.__new__(LLMService)
        items=[{'id':str(i),'content':'Source fact '+str(i),'metadata':{'parent_id':str(i//2)}} for i in range(4)]
        llm._generate_with_fallback=Mock(return_value='{"ids":[2,2,1]}')
        selected=llm.select_scripture_evidence('Question?',items,4,[])
        self.assertEqual(['1','0'],[item['id'] for item in selected])
        payload=json.loads(llm._generate_with_fallback.call_args.args[0])
        self.assertEqual(4,len(payload['sources']))
        self.assertEqual([1,1,2,2],[row['paragraph'] for row in payload['sources']])
        for invalid in ('{"ids":[99]}','{"ids":[true]}','{"ids":"1"}'):
            llm._generate_with_fallback=Mock(return_value=invalid)
            self.assertIsNone(llm.select_scripture_evidence('Question?',items,4,[]))
        llm._generate_with_fallback=Mock(return_value='{"ids":[]}')
        self.assertEqual([],llm.select_scripture_evidence('Question?',items,4,[]))

    def test_selector_batches_full_matched_verses_instead_of_clipping_them(self):
        llm=object.__new__(LLMService)
        items=[{'id':str(i),'content':str(i)+' fact '+('x'*1800),'metadata':{}} for i in range(12)]
        payloads=[]
        def select(prompt,**kwargs):
            payload=json.loads(prompt); payloads.append(payload)
            return '{"ids":[1]}'
        llm._generate_with_fallback=select
        with patch.dict('os.environ',{'OLLAMA_NUM_CTX':'10000','RAG_MAX_OUTPUT_TOKENS':'2048'}):
            selected=llm.select_scripture_evidence('Question?',items,2,[])
        self.assertIsNotNone(selected)
        seen={row['text'] for payload in payloads for row in payload['sources']}
        self.assertTrue(all(item['content'] in seen for item in items))
        self.assertLess(len(payloads),2*len(items))

    def test_expansion_keeps_all_selected_matches_in_one_canonical_paragraph(self):
        items=[{'id':str(i),'content':str(i)+' matched fact','metadata':{'document_type':'scripture','parent_id':'p','verse_label':str(i)}} for i in (1,2)]
        def expand(item,cache):
            return {**item,'content':'1 matched fact\n2 matched fact','metadata':dict(item['metadata'])}
        llm=Mock(); llm.select_scripture_evidence.return_value=items
        with patch.object(ans,'_collect_hybrid_candidates',return_value=('query',items)), patch.object(ans,'get_llm_service',return_value=llm), patch.object(ans.ScriptureStore,'expand',side_effect=expand):
            rows=ans.retrieve_ranked_contexts('X là ai?',top_k=1)
        self.assertEqual(1,len(rows))
        self.assertEqual(['1 matched fact','2 matched fact'],rows[0]['matched_contents'])
        self.assertEqual(['1','2'],rows[0]['matched_labels'])

    def test_semantic_budget_drops_whole_paragraphs_but_keeps_exact_requests(self):
        rows=[{'content':'Complete source paragraph '+str(i),'metadata':{'document_type':'scripture'}} for i in range(3)]
        llm=Mock()
        llm.generate_scripture_response.side_effect=[ContextBudgetError('budget'),{'answer':'Supported. [Nguồn 1]','claims':[],'clarify':False}]
        diagnostics={'retrieval_route':'semantic'}
        with patch.object(ans,'retrieve_ranked_contexts',return_value=rows), patch.object(ans,'get_llm_service',return_value=llm):
            answer,kept=ans.answer_question('Who?',diagnostics=diagnostics)
        self.assertEqual(rows[:2],kept)
        self.assertEqual(rows[:2],llm.generate_scripture_response.call_args.args[1])
        self.assertEqual(1,diagnostics['context_budget_removed_paragraphs'])
        self.assertIn('Supported',answer)
        rows=[{'content':'a'*50,'metadata':{'source':'test','translation':'test','reference':'GEN 1:1','passage_id':'p','last_passage_id':'p'}}]
        with patch.object(ans,'retrieve_ranked_contexts',return_value=rows), patch.object(ans.ScriptureStore,'expand',return_value=rows[0]), patch.dict('os.environ',{'RAG_CONTEXT_MAX_CHARS':'10'}):
            answer,kept=ans.answer_question('Giải thích GEN 1:1',diagnostics={'retrieval_route':'exact_scripture'})
        self.assertEqual(rows,kept)
        self.assertIn('vượt dung lượng',answer)

    def test_unhelpful_trait_gets_one_grounded_repair_without_false_ambiguity(self):
        llm=object.__new__(LLMService)
        sources=[{'content':'1 X hiền lành.\n2 X được giao dẫn dân ra khỏi nước Y.'}]
        llm._generate_with_fallback=Mock(side_effect=[
            '{"clarify":false,"claims":[{"text":"X hiền lành.","evidence":["S1E1"]}]}',
            '{"supported":[1],"sufficient":false,"unsupported_details":[]}',
            '{"clarify":false,"claims":[{"text":"X được giao dẫn dân ra khỏi nước Y.","evidence":["S1E2"]}]}',
            '{"supported":[1],"sufficient":true,"unsupported_details":[]}'])
        result=llm.generate_scripture_response('X là ai?',sources)
        self.assertFalse(result['clarify'])
        self.assertIn('dẫn dân',result['answer'])
        self.assertEqual(4,llm._generate_with_fallback.call_count)
        self.assertNotIn('clarify',llm._generate_with_fallback.call_args.kwargs['response_schema']['properties'])

    def test_unqualified_name_requires_source_evidence_to_ask_for_clarification(self):
        llm=object.__new__(LLMService)
        sources=[{'content':'1 Có ông X-a X-b và ông X-a X-c.'}]
        llm._generate_with_fallback=Mock(return_value='{"different_people":true,"evidence":["S1E1"]}')
        result=llm.generate_scripture_response('X-a là ai?',sources)
        self.assertTrue(result['clarify'])
        self.assertEqual(sources[0]['content'],result['ambiguity_evidence'][0]['quote'])
        self.assertTrue(llm.generate_scripture_response('Bà X-a là ai?',sources)['clarify'])
        for invalid in ('{"different_people":true,"evidence":[]}', '{"different_people":true,"evidence":["S1E99"]}', '{"different_people":false,"evidence":["S1E1"]}'):
            llm._generate_with_fallback=Mock(return_value=invalid)
            self.assertIsNone(llm.generate_scripture_response('X-a là ai?',sources))
        llm._generate_with_fallback=Mock(side_effect=['{"different_people":false,"evidence":[]}',
            '{"clarify":false,"claims":[{"text":"X-a được giao nhiệm vụ.","evidence":["S1E1"]}]}',
            '{"supported":[1],"sufficient":true,"unsupported_details":[]}'])
        self.assertFalse(llm.generate_scripture_response('X-a là ai?',sources)['clarify'])

    def test_grounding_requires_verbatim_evidence_from_the_claims_own_source(self):
        llm=object.__new__(LLMService)
        sources=[{'content':'Bà X là mẹ của Y.\nÔng Z là chồng của X.'},{'content':'Y được trao nhiệm vụ.'}]
        valid={'clarify':False,'claims':[{'text':'X là mẹ của Y.','evidence':['S1E1']}]}
        llm._generate_with_fallback=Mock(side_effect=[json.dumps(valid,ensure_ascii=False),'{"supported":[1],"sufficient":true,"unsupported_details":[],"clarify":false}'])
        result=llm.generate_scripture_response('X là ai?',sources)
        self.assertEqual('X là mẹ của Y. [Nguồn 1]',result['answer'])
        self.assertEqual(sources[0]['content'],result['claims'][0]['quotes'][0])
        review_payload=json.loads(llm._generate_with_fallback.call_args.args[0])
        self.assertEqual([{'id':1,'text':'X là mẹ của Y.','evidence':['S1E1']}],review_payload['claims'])
        self.assertEqual([{'source':1,'details':['Loại: Tài liệu khác'],
                           'excerpts':[{'id':'S1E1','text':sources[0]['content']}],
                           'reviewed_annotations':{}}],review_payload['sources'])
        self.assertEqual([{'source':2,'content':sources[1]['content']}],review_payload['available_context'])
        self.assertNotIn('Y được trao nhiệm vụ.',json.dumps(review_payload['claims'],ensure_ascii=False))
        for invalid in (
            {'clarify':False,'claims':[{**valid['claims'][0],'evidence':['S3E1']}]},
            {'clarify':False,'claims':[{**valid['claims'][0],'evidence':[True]}]},
            {'clarify':False,'claims':[{**valid['claims'][0],'evidence':['S1E99']}]},
            {'clarify':False,'claims':[{**valid['claims'][0],'evidence':[]}]},
            {'clarify':False,'claims':[{**valid['claims'][0],'text':'X nói: "Lời nguồn..."'}]},
            {'clarify':False,'claims':[{**valid['claims'][0],'text':"X nói: 'Lời nguồn...'"}]},
            {'clarify':True,'claims':valid['claims']}, 'not JSON',
        ):
            llm._generate_with_fallback=Mock(return_value=invalid if isinstance(invalid,str) else json.dumps(invalid))
            self.assertIsNone(llm.generate_scripture_response('X là ai?',sources))
        llm._generate_with_fallback=Mock(return_value='{"clarify":false,"claims":[]}')
        self.assertEqual('KHONG_DU_NGUON',llm.generate_scripture_response('Unknown?',sources)['answer'])
        llm._generate_with_fallback=Mock(return_value='{"clarify":true,"claims":[]}')
        self.assertTrue(llm.generate_scripture_response('X?',sources)['clarify'])
        for verdict in ('{"supported":[99],"sufficient":true,"unsupported_details":[],"clarify":false}','{"supported":[true],"sufficient":true,"unsupported_details":[],"clarify":false}'):
            llm._generate_with_fallback=Mock(side_effect=[json.dumps(valid),verdict])
            self.assertIsNone(llm.generate_scripture_response('X?',sources))
        llm._generate_with_fallback=Mock(side_effect=[json.dumps(valid),'{"supported":[],"sufficient":false,"unsupported_details":[],"clarify":false}'])
        self.assertEqual('KHONG_DU_NGUON',llm.generate_scripture_response('X?',sources)['answer'])

        multi={'clarify':False,'claims':[{'text':'X là mẹ của Y và Y được trao nhiệm vụ.','evidence':['S1E1','S2E1']}]}
        llm._generate_with_fallback=Mock(side_effect=[json.dumps(multi),'{"supported":[1],"sufficient":true,"unsupported_details":[],"clarify":false}'])
        grounded=llm.generate_scripture_response('X và Y?',sources)
        self.assertEqual('X là mẹ của Y và Y được trao nhiệm vụ. [Nguồn 1], [Nguồn 2]',grounded['answer'])
        self.assertEqual([sources[0]['content'],sources[1]['content']],grounded['claims'][0]['quotes'])
        two={'clarify':False,'claims':[{'text':'X là mẹ của Y.','evidence':['S1E1']},{'text':'X là cha của Y.','evidence':['S1E1']}]}
        llm._generate_with_fallback=Mock(side_effect=[json.dumps(two),'{"supported":[1],"sufficient":true,"unsupported_details":[],"clarify":false}'])
        self.assertEqual('X là mẹ của Y. [Nguồn 1]',llm.generate_scripture_response('Giải thích X là ai?',sources)['answer'])

    def test_evidence_ids_do_not_reuse_biblical_verse_numbers(self):
        llm=object.__new__(LLMService)
        sources=[{'content':'11 Ông X được giao nhiệm vụ.\n12 Ông làm theo.', 'metadata':{'verse_label':'11','document_type':'scripture','translation':'Bản thử','annotations':json.dumps({'REF':{'origin':'reviewed','speaker':'Người kể'},'UNKNOWN':{'origin':'unknown','speaker':None}})}}]
        llm._generate_with_fallback=Mock(side_effect=[
            '{"clarify":false,"claims":[{"text":"X được giao nhiệm vụ.","evidence":["S1E1"]}]}',
            '{"supported":[1],"sufficient":true,"unsupported_details":[],"clarify":false}',
        ])
        grounded=llm.generate_scripture_response('X được làm gì?',sources)
        self.assertEqual(['11 Ông X được giao nhiệm vụ.'],grounded['claims'][0]['quotes'])
        call=llm._generate_with_fallback.call_args_list[0]
        payload=json.loads(call.args[0])
        self.assertEqual(['S1E1','S1E2'],[part['id'] for part in payload['sources'][0]['excerpts']])
        self.assertEqual(['S1E1'],payload['sources'][0]['matched'])
        self.assertEqual(['REF'],list(payload['sources'][0]['reviewed_annotations']))
        self.assertIn('Bản dịch: Bản thử',payload['sources'][0]['details'])
        self.assertEqual(['S1E1','S1E2'],call.kwargs['response_schema']['properties']['claims']['items']['properties']['evidence']['items']['enum'])

    def test_detected_unsupported_details_trigger_a_bounded_repair(self):
        llm=object.__new__(LLMService)
        sources=[{'content':'1 X làm vua nhà Y.'}]
        sources[0]['matched_contents'] = [sources[0]['content']]
        llm._generate_with_fallback=Mock(side_effect=[
            '{"clarify":false,"claims":[{"text":"X làm vua Y rồi Z.","evidence":["S1E1"]}]}',
            '{"unsupported_details":["rồi Z"],"supported":[1],"sufficient":true}',
            '{"clarify":false,"claims":[{"text":"X làm vua Y.","evidence":["S1E1"]}]}',
            '{"unsupported_details":[],"supported":[1],"sufficient":true}'])
        self.assertEqual('X làm vua Y. [Nguồn 1]',llm.generate_scripture_response('X có vai trò gì?',sources)['answer'])
        self.assertEqual(4,llm._generate_with_fallback.call_count)
        repair = json.loads(llm._generate_with_fallback.call_args_list[2].args[0])
        self.assertEqual(['rồi Z'],repair['review_feedback']['unsupported_details'])
        self.assertEqual(['S1E1'],repair['sources'][0]['matched'])

    def test_explicit_same_name_different_person_is_not_left_to_model_guessing(self):
        llm=object.__new__(LLMService)
        llm._generate_with_fallback=Mock(side_effect=AssertionError('Already explicit in source'))
        source={'content':'1 X-a B-c cùng một người khác cũng tên là X-a.'}
        result=llm.generate_scripture_response('X-a là ai?',[source])
        self.assertTrue(result['clarify'])
        self.assertEqual(source['content'],result['ambiguity_evidence'][0]['quote'])

    def test_identity_titles_are_bound_to_the_asked_name_and_source(self):
        llm=object.__new__(LLMService)
        sources=[{'content':'1 Vua X-a sai người đi. Ông trao nhiệm vụ cho họ.'},{'content':'1 Vua Y-b ở nơi khác.'}]
        facts={'clarify':False,'claims':[{'text':'X-a là vua. Ông sai người đi và trao nhiệm vụ cho họ.','evidence':['S1E1']}]}
        llm._generate_with_fallback=Mock(side_effect=[
            '{"different_people":false,"evidence":[]}',
            json.dumps(facts), '{"supported":[1],"sufficient":true,"unsupported_details":[]}'])
        result=llm.generate_scripture_response('X-a là ai?',sources)
        self.assertEqual(facts['claims'][0]['text']+' [Nguồn 1]', result['answer'])
        self.assertEqual([sources[0]['content']], result['claims'][0]['quotes'])
        payload=json.loads(llm._generate_with_fallback.call_args_list[1].args[0])
        self.assertNotIn('choices',payload)
        self.assertEqual(3,llm._generate_with_fallback.call_count)

    def test_small_lists_use_original_clauses_instead_of_copying_whole_verses(self):
        llm=object.__new__(LLMService)
        sources=[{'content':'4–5 Good, patient, kind.','requested_source':True},
                 {'content':'6 Unrelated, extra, outside.'}]
        llm._generate_with_fallback=Mock(return_value='{"ids":[1,2,3]}')
        result=llm.generate_scripture_response('Nêu ba đặc điểm.',sources)
        self.assertEqual(3,len(result['claims']))
        self.assertEqual(3,len(result['answer'].splitlines()))
        payload=json.loads(llm._generate_with_fallback.call_args_list[0].args[0])
        self.assertEqual(['Good','patient','kind'],[choice['text'] for choice in payload['choices']])
        self.assertEqual(sources[0]['content'],result['claims'][0]['quotes'][0])
        self.assertEqual([{'source':1,'content':sources[0]['content']}],payload['passages'])
        llm._generate_with_fallback=Mock(return_value='{"ids":[1,1,1]}')
        self.assertIsNone(llm.generate_scripture_response('Nêu ba đặc điểm.',sources))

    def test_small_numbered_lists_require_one_claim_per_requested_item(self):
        llm=object.__new__(LLMService)
        llm._generate_with_fallback=Mock(return_value='{"clarify":false,"claims":[{"text":"First and second.","evidence":["S1E1"]}]}')
        self.assertIsNone(llm.generate_scripture_response('Nêu ba đặc điểm.',[{'content':'1 First, second, third.'}]))

    def test_ollama_binds_structured_output_without_changing_normal_answers(self):
        provider=object.__new__(_OllamaProvider)
        provider.num_ctx=16384; provider.max_output_tokens=2048
        provider.client=Mock()
        provider.client.invoke.return_value.content='normal'
        provider.client.bind.return_value.invoke.return_value.content='{"value":1}'
        schema={'type':'object','properties':{'value':{'type':'integer'}},'required':['value']}
        self.assertEqual('{"value":1}',provider.generate('data',system_prompt='rules',response_schema=schema))
        provider.client.bind.assert_called_once_with(format=schema)
        self.assertEqual('normal',provider.generate('data',system_prompt='rules'))

    def test_structured_schema_reaches_ollama_with_existing_options(self):
        from langchain_ollama import ChatOllama
        from types import SimpleNamespace
        provider=object.__new__(_OllamaProvider)
        provider.num_ctx=16384; provider.max_output_tokens=2048
        provider.client=ChatOllama(model='test',num_ctx=16384,num_predict=2048,temperature=0.25,reasoning=False)
        schema={'type':'object','properties':{'value':{'type':'integer'}}}
        with patch.object(ChatOllama,'invoke',autospec=True,return_value=SimpleNamespace(content='{"value":1}')) as invoke:
            provider.generate('data',system_prompt='rules',response_schema=schema)
        client,messages=invoke.call_args.args[:2]
        request=client._chat_params(messages,None,**invoke.call_args.kwargs)
        self.assertEqual(2048,request['options']['num_predict'])
        self.assertEqual(16384,request['options']['num_ctx'])
        self.assertEqual(0.25,request['options']['temperature'])
        self.assertFalse(request['think'])
        self.assertEqual(schema,request['format'])

    def test_basic_and_explanatory_answers_allow_multiple_checked_facts(self):
        llm=object.__new__(LLMService)
        sources=[{'content':'X là mẹ của Y. X sống ở Z.'}]
        facts={'clarify':False,'claims':[{'text':'X là mẹ của Y.','evidence':['S1E1']},{'text':'X sống ở Z.','evidence':['S1E1']}]}
        for question in ('X là ai?','X làm việc đó như thế nào?','Giải thích X là ai?',
                         'Nêu hai đặc điểm của X.','Liệt kê tên trong nhóm.','X khác nhau thế nào?'):
            llm._generate_with_fallback=Mock(side_effect=[json.dumps(facts),'{"supported":[1,2],"sufficient":true,"unsupported_details":[]}'])
            self.assertEqual(2,len(llm.generate_scripture_response(question,sources)['claims']))

    def test_explanation_paragraphs_keep_their_own_verified_citations(self):
        llm=object.__new__(LLMService)
        sources=[{'content':'X là mẹ của Y.'}, {'content':'X sống ở Z.'}, {'content':'X chăm sóc Y.'}]
        facts={'clarify':False,'claims':[{'text':row['content'],'evidence':[f'S{index}E1']}
                                       for index,row in enumerate(sources,1)]}
        llm._generate_with_fallback=Mock(side_effect=[json.dumps(facts),'{"supported":[1,2,3],"sufficient":true,"unsupported_details":[]}'])
        result=llm.generate_scripture_response('X là ai?',sources)
        self.assertEqual([row['content']+f' [Nguồn {index}]' for index,row in enumerate(sources,1)], result['answer'].split('\n\n'))
        self.assertEqual([[1],[2],[3]], [claim['sources'] for claim in result['claims']])

    def test_detailed_answers_allow_six_checked_facts_and_reject_excess(self):
        llm=object.__new__(LLMService)
        sources=[{'content':'\n'.join(f'{index} X thực hiện công việc {index}.' for index in range(1,8))}]
        facts={'clarify':False,'claims':[{'text':f'X thực hiện công việc {index}.','evidence':[f'S1E{index}']}
                                       for index in range(1,7)]}
        for question in ('Giải thích vai trò của X.', 'Trình bày đầy đủ về X.', 'X làm gì? Nêu chi tiết.'):
            with self.subTest(question=question):
                llm._generate_with_fallback=Mock(side_effect=[json.dumps(facts),'{"supported":[1,2,3,4,5,6],"sufficient":true,"unsupported_details":[]}'])
                result=llm.generate_scripture_response(question,sources)
                self.assertEqual(6,len(result['claims']))
                self.assertEqual(6,len(result['answer'].split('\n\n')))
        facts['claims'].append({'text':'X thực hiện công việc 7.','evidence':['S1E7']})
        llm._generate_with_fallback=Mock(return_value=json.dumps(facts))
        self.assertIsNone(llm.generate_scripture_response('Giải thích vai trò của X.',sources))

    def test_scripture_answer_never_falls_back_to_an_unsupported_draft(self):
        llm=Mock(); llm.generate_response.return_value='Wrong relation. [Nguồn 1]'
        rows=[{'content':'Source fact.','metadata':{'document_type':'scripture'}}]
        with patch.object(ans,'retrieve_ranked_contexts',return_value=rows), patch.object(ans,'get_llm_service',return_value=llm):
            llm.generate_scripture_response.return_value=None
            diagnostics={}
            answer,_=ans.answer_question('Who?',diagnostics=diagnostics)
            self.assertNotIn('Wrong relation',answer)
            self.assertEqual('grounding_rejected',diagnostics['answer_validation'])
            llm.generate_scripture_response.return_value={'answer':'Supported role. [Nguồn 1]','claims':[],'clarify':False}
            answer,_=ans.answer_question('Who?',diagnostics=diagnostics)
            self.assertEqual('Supported role. [Nguồn 1]',answer)
            self.assertEqual('citation_and_quote_checked',diagnostics['answer_validation'])

    def test_heading_search_uses_the_committed_run_and_preserves_verbatim_text(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ',{'EMBEDDING_MODEL_NAME':'test/model','EMBEDDING_DIMENSION':'3'}):
            db=VectorDatabase('heading-search',db_path=folder)
            try:
                verse={'book_code':'GEN','chapter':1,'verse_label':'1','text':'Một người đi đường.'}
                passage={'id':'same-passage','title':'Người nhân hậu','origin':'source_structure'}
                data=ScriptureData(verses=[verse],passages=[passage])
                ScriptureStore(db).write('active','doc','edition','source.pdf',data,{})
                data.passages=[{**passage,'title':'Tiêu đề chưa công bố'}]
                ScriptureStore(db).write('uncommitted','doc','edition','source.pdf',data,{})
                metadata={'ingest_schema_version':5,'document_type':'scripture','processing_version':4,'ingest_run_id':'active','passage_id':'same-passage'}
                db.add_vectors(['Một người đi đường.','Vũ trụ xa xôi.','Một tập tin khác.'],[[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]],
                               [metadata,{'ingest_schema_version':5},{'ingest_schema_version':5}],['source','other','third'])
                db.set_collection_state('ready')
                rows=db.search_by_bm25('nhân hậu')
                self.assertEqual('source',rows[0]['id'])
                self.assertEqual('Một người đi đường.',rows[0]['content'])
                self.assertEqual('Người nhân hậu',rows[0]['metadata']['heading'])
                self.assertFalse(db.search_by_bm25('chưa công bố'))
            finally:
                db.client.close()

    def test_ambiguous_spelling_keeps_all_compatible_names_in_search_vocabulary(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'EMBEDDING_MODEL_NAME':'test/model','EMBEDDING_DIMENSION':'3'}):
            db = VectorDatabase('ambiguous-name-vocabulary', db_path=folder)
            try:
                db.add_vectors(['Ông Si-môn X-a. Ông Si-mon X-b. Ông Ê-đen.'],
                               [[1.] * db.embedding_dimension], [{'ingest_schema_version':5}], ['names'])
                db.set_collection_state('ready')
                for question in ('si mon là ai?', 'SIMON là ai?', 'Si — Mon là ai?'):
                    with self.subTest(question=question):
                        self.assertEqual(question, db.normalize_query_names(question))
                        vocabulary = db.get_search_vocabulary(question)
                        self.assertTrue({'Si-môn','Si-mon','Si-môn X-a','Si-mon X-b'} <= set(vocabulary))
                        self.assertNotIn('Si-môn X-b', vocabulary)
                vocabulary = db.get_search_vocabulary('si môn là ai?')
                self.assertEqual('Si-môn', vocabulary[0])
                self.assertNotIn('Si-mon', vocabulary)
                vocabulary = db.get_search_vocabulary('má ri a là ai?')
                self.assertNotIn('Si-mon', vocabulary)
            finally:
                db.client.close()

    def test_search_vocabulary_extends_an_unqualified_name_only_from_loaded_text(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ',{'EMBEDDING_MODEL_NAME':'test/model','EMBEDDING_DIMENSION':'3'}):
            db=VectorDatabase('name-vocabulary',db_path=folder)
            try:
                texts=['X-a X-b đi đường.','X-a nói với Y-c.','Một người khác.']
                db.add_vectors(texts,[[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]],
                               [{'ingest_schema_version':5}]*3,['one','two','three'])
                db.set_collection_state('ready')
                vocabulary=db.get_search_vocabulary('X-a là ai?')
                self.assertIn('X-a X-b',vocabulary)
                self.assertNotIn('X-a Y-c',vocabulary)
                self.assertEqual(texts,[row['content'] for row in db.get_all_documents()])
            finally:
                db.client.close()


if __name__=='__main__':
    unittest.main()
