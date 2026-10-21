import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from services import ans
from services.model import LLMService, _OllamaProvider, _GoogleProvider, _HuggingFaceProvider, LLMOutputError, LLMRequestError, ContextBudgetError
from services.evaluation_review import review_digest, apply_review
from services.llm_usage import measure_llm_usage


class ReleaseSafetyTests(unittest.TestCase):
    def test_requested_items_cannot_differ_only_by_spacing_case_or_final_punctuation(self):
        llm=object.__new__(LLMService)
        llm._generate_with_fallback=Mock(return_value=json.dumps({'clarify':False,'insufficient_evidence':False,
            'claims':[{'text':'X hiền lành.','evidence':['S1E1']},
                      {'text':'x  hiền lành','evidence':['S1E1']}]}))
        rows=[{'content':'X hiền lành.','metadata':{'document_type':'commentary'}}]
        self.assertIsNone(llm.generate_scripture_response('Nêu hai đặc điểm của X.',rows))
        llm._generate_with_fallback.assert_called_once()

    def test_explicit_abstention_is_distinct_from_a_failed_claim_and_cannot_include_claims(self):
        llm=object.__new__(LLMService)
        llm._generate_with_fallback=Mock(return_value=json.dumps(
            {'clarify':False,'insufficient_evidence':True,'claims':[]}))
        sources=[{'content':'The document does not name a founder.','metadata':{'document_type':'generic'}}]
        result=llm.generate_scripture_response('Who founded this project?',sources)
        self.assertEqual('insufficient_evidence',result['validation_reason'])
        self.assertEqual('KHONG_DU_NGUON',result['answer'])
        llm._generate_with_fallback.assert_called_once()
        llm._generate_with_fallback.return_value=json.dumps({'clarify':False,'insufficient_evidence':True,
            'claims':[{'text':'A founded it.','evidence':['S1E1']}]})
        self.assertIsNone(llm.generate_scripture_response('Who founded this project?',sources))

    def test_generic_source_cannot_bypass_claim_review(self):
        rows = [{'content':'1 X lived in A.', 'metadata':{'document_type':'scripture'}},
                {'content':'Cover page.', 'metadata':{'document_type':'generic'}}]
        llm = Mock()
        llm.generate_response.return_value = 'X lived in B. [Nguồn 1]'
        llm.generate_scripture_response.return_value = None
        with patch.object(ans,'retrieve_ranked_contexts',return_value=rows), patch.object(ans,'get_llm_service',return_value=llm):
            diagnostics = {}
            answer, _ = ans.answer_question('Where did X live?', diagnostics=diagnostics)
        self.assertNotIn('X lived in B', answer)
        self.assertEqual('grounding_rejected', diagnostics['answer_validation'])
        llm.generate_response.assert_not_called()
        llm.generate_scripture_response.assert_called_once()

    def test_review_cannot_reduce_a_requested_list_to_one_item(self):
        llm = object.__new__(LLMService)
        draft = {'clarify':False,'claims':[{'text':'X hiền lành.','evidence':['S1E1']},
                                          {'text':'X công bằng.','evidence':['S1E2']}]}
        partial_review = {'supported':[1], 'sufficient':True, 'unsupported_details':[]}
        llm._generate_with_fallback = Mock(side_effect=[json.dumps(draft),json.dumps(partial_review)]*2)
        result = llm.generate_scripture_response('Nêu hai đặc điểm của X.',
                  [{'content':'1 X hiền lành.\n2 X công bằng.','metadata':{'document_type':'commentary'}}])
        self.assertEqual('KHONG_DU_NGUON',result['answer'])
        self.assertEqual(4,llm._generate_with_fallback.call_count)

    def test_truncated_ollama_output_is_a_provider_error(self):
        provider = object.__new__(_OllamaProvider)
        provider.num_ctx=16384; provider.max_output_tokens=2048
        provider.client=Mock()
        provider.client.invoke.return_value=SimpleNamespace(content='partial',response_metadata={'done_reason':'length'})
        with self.assertRaises(LLMOutputError): provider.generate('question')

    def test_truncated_google_output_is_rejected(self):
        provider=object.__new__(_GoogleProvider); provider.model_name='test'; provider.client=Mock()
        provider.client.models.generate_content.return_value=SimpleNamespace(
            text='partial',usage_metadata=None,candidates=[SimpleNamespace(finish_reason='MAX_TOKENS')])
        with self.assertRaises(LLMOutputError): provider.generate('question')

    def test_hf_checks_generated_tokens_and_eos_at_the_limit(self):
        import torch
        class Inputs(dict):
            def to(self,device): return self
        tokenizer=Mock(return_value=Inputs(input_ids=torch.tensor([[1,2]])))
        tokenizer.decode.return_value='Completed'
        model=SimpleNamespace(device='cpu',generation_config=SimpleNamespace(eos_token_id=[9]),
                              generate=Mock(return_value=torch.tensor([[1,2,3,4]])))
        provider=object.__new__(_HuggingFaceProvider)
        provider.client=SimpleNamespace(tokenizer=tokenizer,model=model)
        provider.max_new_tokens=2; provider.temperature=0; provider.top_p=0.9
        with self.assertRaises(LLMOutputError): provider.generate('q')
        model.generate.return_value=torch.tensor([[1,2,3,9]])
        self.assertEqual('Completed',provider.generate('q'))

    def test_quota_error_is_sanitized_and_requests_are_counted(self):
        llm=object.__new__(LLMService)
        error=RuntimeError('secret response body'); error.code=429
        provider=object.__new__(_GoogleProvider); provider.client=Mock(); provider.model_name='fake'
        provider.client.models.generate_content.side_effect=error
        llm.providers=[provider]
        with measure_llm_usage() as usage, self.assertRaises(LLMRequestError) as caught:
            llm._generate_with_fallback('question')
        self.assertNotIn('secret',str(caught.exception))
        self.assertEqual(1,usage['requests'])

    def test_local_context_rejection_is_not_counted_as_an_api_request(self):
        provider=object.__new__(_OllamaProvider)
        provider.num_ctx=1024; provider.max_output_tokens=256; provider.client=Mock()
        with measure_llm_usage() as usage, self.assertRaises(ContextBudgetError):
            provider.generate('x'*2000,system_prompt='System')
        self.assertEqual(0,usage['requests'])
        provider.client.invoke.assert_not_called()

    def test_context_budget_reports_the_local_bound_without_a_provider_failure_log(self):
        provider=object.__new__(_OllamaProvider)
        provider.num_ctx=1024; provider.max_output_tokens=256; provider.client=Mock()
        llm=object.__new__(LLMService); llm.providers=[provider]
        with patch('builtins.print') as console, self.assertLogs('services.model',level='DEBUG'), self.assertRaises(ContextBudgetError) as caught:
            llm._generate_with_fallback('x'*2000,system_prompt='System')
        console.assert_not_called()
        provider.client.invoke.assert_not_called()
        self.assertIn('256 byte',str(caught.exception))
        self.assertIn('1024 token',str(caught.exception))

    def test_six_claims_share_full_review_evidence_without_exceeding_context(self):
        facts=[f'X thực hiện nhiệm vụ {index}.' for index in range(1,7)]
        paragraph=' '.join(facts) + ' Văn mạch của nguồn được giữ đầy đủ.'*22
        draft={'clarify':False,'insufficient_evidence':False,'claims':[
            {'text':text,'evidence':['S1E1']} for text in facts]}
        verdict={'unsupported_details':[],'supported':[1,2,3,4,5,6],'sufficient':True}
        provider=object.__new__(_OllamaProvider)
        provider.num_ctx=16384; provider.max_output_tokens=2048; provider.client=Mock()
        provider.client.bind.return_value=provider.client
        payloads=[]
        def respond(messages):
            payload=json.loads(messages[1].content)
            payloads.append(payload)
            return SimpleNamespace(content=json.dumps(verdict if 'claims' in payload else draft),response_metadata={})
        provider.client.invoke.side_effect=respond
        llm=object.__new__(LLMService); llm.providers=[provider]
        result=llm.generate_scripture_response('Giải thích vai trò của X.',[
            {'content':paragraph,'metadata':{'document_type':'commentary'}}])
        self.assertEqual(6,len(result['claims']))
        self.assertEqual(2,len(payloads))
        review=payloads[1]
        self.assertEqual(1,len(review['sources']))
        self.assertEqual(paragraph,review['sources'][0]['excerpts'][0]['text'])
        self.assertTrue(all(claim['evidence']==['S1E1'] for claim in review['claims']))
        self.assertEqual(1,json.dumps(review,ensure_ascii=False).count(paragraph))
        old_copies=[{'quotes':[paragraph],'paragraphs':[{'content':paragraph}]} for _ in facts]
        self.assertGreater(len(json.dumps(old_copies,ensure_ascii=False).encode('utf-8')),
                           provider.num_ctx-provider.max_output_tokens-512)

    def test_malformed_json_is_not_accepted_from_a_provider(self):
        llm=object.__new__(LLMService)
        llm.providers=[SimpleNamespace(name='hf',generate=Mock(return_value='{"value":"wrong"}'))]
        with self.assertRaises(LLMRequestError):
            llm._generate_with_fallback('q',response_schema={'type':'object','properties':{'value':{'type':'integer'}}})

    def test_invalid_key_missing_model_and_timeout_are_provider_errors(self):
        for status in (400,401,403,404,None):
            with self.subTest(status=status):
                llm=object.__new__(LLMService)
                error=TimeoutError('private response') if status is None else RuntimeError('private response')
                if status is not None: error.code=status
                llm.providers=[SimpleNamespace(name='google',generate=Mock(side_effect=error))]
                with self.assertRaises(LLMRequestError) as caught:
                    llm._generate_with_fallback('q')
                self.assertNotIn('private',str(caught.exception))

    def test_benchmark_uses_the_active_answer_provider(self):
        ans.get_benchmark_llm_service.cache_clear()
        with patch.object(ans,'get_llm_service') as active:
            self.assertIs(active.return_value,ans.get_benchmark_llm_service())
        ans.get_benchmark_llm_service.cache_clear()

    def test_human_review_is_bound_to_the_exact_answer_and_sources(self):
        result={'id':'a','question':'Q','answer':'A','sources':[],'regression_passed':True}
        review={'reviewed_by':'Reader','review_digest':review_digest(result),
                'correct':True,'supported':True,'complete':True,'scope_preserved':True}
        self.assertTrue(apply_review(result,review)['passed'])
        with self.assertRaises(ValueError): apply_review({**result,'answer':'different'},review)

    def test_keyword_match_does_not_certify_a_negated_answer(self):
        from evaluate_bible_answers import evaluate
        def recorded(question,diagnostics,**kwargs):
            diagnostics['answer_validation']='citation_and_quote_checked'
            return 'X was not a king. [Nguồn 1]',[{'content':'X was a king.','metadata':{'references':'["GEN 1:1"]'}}]
        with patch.object(ans,'answer_question',side_effect=recorded):
            result=evaluate({'id':'negative','question':'Who?','expected_references':['GEN 1:1'],'answer_terms':['king']},True)
        self.assertTrue(result['regression_passed'])
        self.assertIsNone(result['passed'])
        self.assertEqual('pending_human_review',result['review_status'])

    def test_reranking_keeps_evidence_at_the_end_of_a_long_passage(self):
        tokenizer=SimpleNamespace(encode=lambda text,**kwargs:list(text),
                                  decode=lambda tokens,**kwargs:''.join(tokens),
                                  num_special_tokens_to_add=lambda **kwargs:3)
        ranker=SimpleNamespace(tokenizer=tokenizer,max_length=32,
                    predict=lambda pairs,**kwargs:[0.9 if 'TAIL' in text else 0.1 for _,text in pairs])
        row={'id':'long','content':'a '*100+'TAIL','metadata':{}}
        diagnostics={}
        with patch.object(ans,'_get_reranker',return_value=ranker),patch.dict('os.environ',{'RERANK_ENABLED':'true'}):
            result=ans._rerank_with_cross_encoder('Q',[row],diagnostics)
        self.assertEqual(0.9,result[0]['rerank_score'])
        self.assertEqual(1,diagnostics['rerank_overflow_candidates'])
        self.assertEqual(0,diagnostics['rerank_truncated_tokens'])
