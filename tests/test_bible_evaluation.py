import unittest
from unittest.mock import patch
from evaluate_bible_answers import evaluate
from services import ans


class BibleEvaluationTests(unittest.TestCase):
    def run_case(self,case,answer,rows,validation='citation_and_quote_checked'):
        def recorded(question,diagnostics,**kwargs):
            diagnostics['answer_validation']=validation
            return answer,rows
        with patch.object(ans,'answer_question',side_effect=recorded) as call:
            result=evaluate(case,answers=True)
        return result,call

    def test_every_reference_group_is_required_for_comparison(self):
        case={'id':'compare','question':'Compare sources','expected_reference_groups':[['GEN 1:1'],['JHN 1:1']]}
        rows=[{'content':'first','metadata':{'references':'["GEN 1:1"]'}}]
        result,_=self.run_case(case,'Answer [Nguồn 1]',rows)
        self.assertEqual([True,False],result['reference_group_hits'])
        self.assertFalse(result['regression_passed'])
        rows.append({'content':'second','metadata':{'references':'["JHN 1:1"]'}})
        self.assertTrue(self.run_case(case,'Answer [Nguồn 1], [Nguồn 2]',rows)[0]['regression_passed'])

    def test_framework_rejection_is_not_a_successful_negative_answer(self):
        case={'id':'unknown','question':'Unknown fact','unanswerable':True}
        for validation in ('rejected','grounding_rejected'):
            result,_=self.run_case(case,'Chưa thể cung cấp diễn giải có dẫn chứng hợp lệ.',[],validation)
            self.assertFalse(result['regression_passed'])
        result,_=self.run_case(case,'Nguồn chưa đủ bằng chứng để trả lời.',[],'insufficient_evidence')
        self.assertTrue(result['regression_passed'])
        result,_=self.run_case(case,'Tài liệu không nêu ngân sách của dự án. [Nguồn 1]',[], 'citation_and_quote_checked')
        self.assertTrue(result['regression_passed'])
        self.assertIsNone(result['passed'])

    def test_history_and_scope_are_forwarded_and_scope_leaks_fail(self):
        history=[{'role':'user','content':'Previous'}]
        case={'id':'scope','question':'Q','chat_history':history,'edition_id':'chosen','expected_references':['GEN 1:1']}
        rows=[{'content':'source','metadata':{'references':'["GEN 1:1"]','edition_id':'wrong'}}]
        result,call=self.run_case(case,'Answer [Nguồn 1]',rows)
        self.assertFalse(result['regression_passed'])
        self.assertFalse(result['scope_preserved'])
        self.assertEqual(history,call.call_args.kwargs['chat_history'])
        self.assertEqual('chosen',call.call_args.kwargs['edition_id'])

    def test_a_list_is_graded_by_enough_distinct_valid_properties(self):
        case={'id':'list','question':'Three properties','expected_references':['GEN 1:1'],
              'answer_term_options':['kind','patient','hopeful','faithful'],'minimum_term_hits':3}
        rows=[{'content':'source','metadata':{'references':'["GEN 1:1"]'}}]
        self.assertFalse(self.run_case(case,'kind and patient [Nguồn 1]',rows)[0]['regression_passed'])
        self.assertTrue(self.run_case(case,'patient, hopeful and faithful [Nguồn 1]',rows)[0]['regression_passed'])
        self.assertFalse(self.run_case({**case,'maximum_term_hits':3},'kind, patient, hopeful and faithful [Nguồn 1]',rows)[0]['regression_passed'])

    def test_verbatim_reading_is_explicit_and_does_not_pass_an_explanation(self):
        case={'id':'read','question':'Explain','expected_references':['GEN 1:1'],'answer_terms':['king']}
        rows=[{'content':'king','metadata':{'references':'["GEN 1:1"]','reference':'GEN 1:1','source':'source.pdf'}}]
        def recorded(question,diagnostics,**kwargs):
            diagnostics['retrieval_route']='exact_scripture'
            return ans.render_scripture(rows),rows
        with patch.object(ans,'answer_question',side_effect=recorded):
            self.assertIs(False,evaluate(case,True)['regression_passed'])
            self.assertTrue(evaluate({**case,'verbatim':True},True)['regression_passed'])

    def test_source_quotes_do_not_hide_an_incomplete_generated_answer(self):
        case={'id':'qa','question':'Who?','expected_references':['GEN 1:1'],'answer_terms':['king']}
        rows=[{'content':'king','metadata':{'references':'["GEN 1:1"]'}}]
        def recorded(question,diagnostics,**kwargs):
            diagnostics.update(answer_validation='citation_and_quote_checked',grounding_evidence=[{'text':'A son.'}])
            return 'Quoted source: king. Interpretation: A son. [Nguồn 1]',rows
        with patch.object(ans,'answer_question',side_effect=recorded):
            self.assertFalse(evaluate(case,True)['regression_passed'])


if __name__=='__main__':
    unittest.main()
