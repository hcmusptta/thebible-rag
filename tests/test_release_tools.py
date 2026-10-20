import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from services import ans
from services.benchmark_manifest import run_manifest, save_benchmark_report, validate_testset_sources
from scripts.audit_corpus import audit
from scripts.check_release import check
from scripts.probe_models import gpu_status, system_memory
from services.evaluation_review import apply_review, review_digest, calibration_summary
from services.llm_usage import record_request
from scripts.benchmark_ablation import run_case


class ReleaseToolsTests(unittest.TestCase):
    def test_ablation_keeps_request_count_and_time_when_provider_fails(self):
        def fail(*args,**kwargs):
            record_request('ollama')
            raise TimeoutError('private response')
        with patch.object(ans,'retrieve_ranked_contexts',side_effect=fail):
            row=run_case({'id':'failed','question':'Q'},'full')
        self.assertEqual('TimeoutError',row['error'])
        self.assertEqual(1,row['llm_usage']['requests'])
        self.assertGreaterEqual(row['seconds'],0)
        self.assertNotIn('private',json.dumps(row))
    def test_benchmark_does_not_score_a_provider_error_message(self):
        from services import eval_ragas
        def failed_answer(*args,diagnostics,**kwargs):
            diagnostics['answer_validation']='provider_error'
            return 'Provider error', [{'content':'Source'}]
        with patch.object(eval_ragas.default_db,'count',return_value=1), \
             patch.object(eval_ragas,'_resolve_golden_testset_path',return_value=__file__), \
             patch.object(eval_ragas,'_load_golden_testset',return_value=[{'id':'a','user_input':'Q'}]), \
             patch.object(eval_ragas,'run_manifest',return_value={'source_catalog':{}}), \
             patch.object(eval_ragas,'answer_question',side_effect=failed_answer), \
             patch.object(eval_ragas,'evaluate') as score:
            with self.assertRaisesRegex(RuntimeError,'Provider'):
                eval_ragas.run_ragas_benchmark(sample_size=1,metric_keys=['faithfulness'])
        score.assert_not_called()
    def test_ablation_reuses_retrieval_and_exports_reviewable_variants(self):
        rows=[{'content':'Atlas keeps documents.','metadata':{}}]
        llm=Mock()
        llm.generate_response.return_value='Draft'
        llm.generate_scripture_response.return_value={'answer':'Checked','claims':[{'text':'Checked'}]}
        with patch.object(ans,'retrieve_ranked_contexts',return_value=rows) as retrieve, \
             patch.object(ans,'get_llm_service',return_value=llm):
            row=run_case({'id':'one','question':'Where?','document_type':'generic',
                          'expected_source_texts':['Atlas keeps documents.']},'hybrid',answers=True)
        retrieve.assert_called_once()
        variants=row['answer_results']
        self.assertEqual(2,len(variants))
        self.assertEqual('Draft',variants[0]['answer'])
        self.assertNotEqual(variants[0]['review_digest'],variants[1]['review_digest'])
        self.assertTrue(all(r['passed'] is None for r in variants))
        llm.generate_scripture_response.side_effect=RuntimeError('private provider response')
        with patch.object(ans,'retrieve_ranked_contexts',return_value=rows), \
             patch.object(ans,'get_llm_service',return_value=llm):
            failed=run_case({'id':'one','question':'Q','expected_source_texts':['Atlas keeps documents.']},'hybrid',True)
        self.assertTrue(failed['retrieval_hit'])
        self.assertEqual('Draft',failed['answer_results'][0]['answer'])
        self.assertEqual('RuntimeError',failed['answer_results'][1]['error'])
        self.assertNotIn('private',json.dumps(failed))

    def test_old_source_or_revision_is_rejected_and_missing_provenance_stays_pending(self):
        rows=[{'id':'old','metadata':{'source':'old.pdf'}},
              {'id':'wrong','metadata':{'source':'current.pdf','document_id':'previous'}},
              {'id':'unknown','metadata':{}}]
        report=validate_testset_sources(rows,{'current.pdf':{'document_id':'current'}})
        self.assertEqual(['source','document_id'],[r['field'] for r in report['mismatches']])
        self.assertIn('unknown',report['unverified_rows'])
        self.assertEqual('pending_human_review',report['semantic_source_review'])

    def test_bm25_ablation_does_not_call_embedding_or_llm(self):
        with patch.object(ans,'get_default_embedder') as embedder,patch.object(ans,'get_llm_service') as llm, \
             patch.object(ans.default_db,'search_by_bm25',return_value=[]) as search:
            _,rows=ans._collect_hybrid_candidates('original',retrieval_profile='bm25')
        self.assertEqual([],rows)
        embedder.assert_not_called(); llm.assert_not_called(); search.assert_called_once()

    def test_manifest_identifies_code_dataset_and_storage_without_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cases.json'; path.write_text('[]')
            db=SimpleNamespace(collection=SimpleNamespace(metadata={'embedding_model':'model','index_revision':'revision'}))
            report=run_manifest(path,db)
            self.assertEqual(64,len(report['code_sha256']))
            self.assertEqual(64,len(report['testset']['sha256']))
            self.assertEqual('revision',report['collection_identity']['index_revision'])
            self.assertNotIn('API_KEY',json.dumps(report))
            save_benchmark_report({'manifest':report,'rows':[],'details':None},Path(folder)/'report.json')
            self.assertEqual([],json.loads((Path(folder)/'report.json').read_text())['metric_rows'])

    def test_release_check_reports_only_secret_location(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'.env copy').write_text('secret')
            with patch('scripts.check_release.subprocess.check_output',side_effect=[b'.env copy\x00',b'']):
                report=check(root)
            self.assertFalse(report['working_tree_checks_passed'])
            self.assertNotIn('secret',json.dumps(report))

    def test_historical_credential_keeps_publication_gate_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('scripts.check_release.subprocess.check_output',side_effect=[
                b'loaddocs/document.py\x00loaddocs/embedding.py\x00',b'oldcommit\n']), \
                 patch('scripts.check_release.subprocess.run',return_value=SimpleNamespace(
                     returncode=0,stdout=b'AIza'+b'1'*32)):
                report=check(Path(folder))
        self.assertTrue(report['working_tree_checks_passed'])
        self.assertFalse(report['publication_file_gate_passed'])
        self.assertEqual(['oldcommit'],report['credential_history_commits'])

    def test_empty_corpus_is_not_certified_complete(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with sqlite3.connect(root/'chroma.sqlite3') as c:
                c.executescript('CREATE TABLE embeddings(id,segment_id); CREATE TABLE segments(id,collection); CREATE TABLE collections(id,name); CREATE TABLE embedding_metadata(id,key,string_value);')
            c.close()
            report=audit(root,'empty')
            self.assertEqual([],report['sources'])
            self.assertEqual('pending',report['human_acceptance'])

    def test_hardware_probe_handles_missing_nvidia_tools(self):
        with patch('scripts.probe_models.subprocess.run',side_effect=FileNotFoundError):
            self.assertEqual([],gpu_status())
        memory=system_memory()
        if memory:
            self.assertGreater(memory['total_bytes'],0)

    def test_calibration_counts_false_accepts_and_pending_reviews(self):
        row={'id':'a','question':'Q','answer':'A','sources':[],'regression_passed':True,
             'diagnostics':{'answer_validation':'citation_and_quote_checked'}}
        review={'reviewed_by':'Reader','review_digest':review_digest(row),'correct':False,
                'supported':False,'complete':True,'scope_preserved':True}
        reviewed=apply_review(row,review)
        summary=calibration_summary([reviewed,{'passed':None}])
        self.assertEqual(1,summary['judge_false_accepts'])
        self.assertEqual(1,summary['judge_false_accepts_by_criterion']['correct'])
        self.assertEqual(0,summary['judge_false_accepts_by_criterion']['complete'])
        self.assertEqual(1,summary['pending'])


if __name__=='__main__': unittest.main()
