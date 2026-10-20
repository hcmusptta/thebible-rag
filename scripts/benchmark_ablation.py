"""Compare retrieval profiles on identical cases; output is diagnostic, never human acceptance."""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services import ans
from services.benchmark_manifest import run_manifest
from services.llm_usage import measure_llm_usage
from services.evaluation_review import review_digest
from evaluate_bible_answers import evaluate

PROFILES=('bm25','vector','hybrid','hybrid_rerank','full')


def run_case(case, profile, answers=False):
    diagnostics={}
    started=time.monotonic()
    try:
        with measure_llm_usage() as usage:
            rows=ans.retrieve_ranked_contexts(case['question'],chat_history=case.get('chat_history'),
                document_type=case.get('document_type','scripture'),edition_id=case.get('edition_id'),
                translation=case.get('translation'),top_k=ans.DEFAULT_TOP_K,
                candidate_limit=ans.DEFAULT_CANDIDATE_LIMIT,diagnostics=diagnostics,retrieval_profile=profile)
    except Exception as error:
        return {'id':case['id'],'question':case['question'],'profile':profile,'passed':False,
                'error':type(error).__name__,'seconds':time.monotonic()-started,
                'llm_usage':usage,'diagnostics':diagnostics}
    # Reuse exactly these candidates for the reference diagnostics; do not retrieve twice.
    diagnostic=evaluate(case,answers=False,retrieved_rows=rows)
    result={**diagnostic,'profile':profile,'seconds':time.monotonic()-started,
            'llm_usage':usage,'diagnostics':diagnostics,'passed':None}
    if answers and rows:
        llm=ans.get_llm_service()
        result['answer_results']=[]
        for label in ('unverified_draft','checked_claims'):
            started=time.monotonic()
            answer, grounded, error_type = '', None, None
            with measure_llm_usage() as usage:
                try:
                    if label=='unverified_draft':
                        answer=llm.generate_response(ans._format_context(rows),case['question'],case.get('chat_history'))
                    else:
                        grounded=llm.generate_scripture_response(case['question'],rows)
                        answer=grounded['answer'] if grounded else ''
                except Exception as error:
                    error_type=type(error).__name__
            variant={**diagnostic,'id':f"{case['id']}:{profile}:{label}",'answer':answer,
                     'seconds':time.monotonic()-started,'llm_usage':usage,'passed':None,
                     'review_status':'pending_human_review','variant':label,
                     'diagnostics':{'answer_validation': 'unverified_offline' if label=='unverified_draft' else
                         ('citation_and_quote_checked' if grounded and grounded.get('claims') else 'grounding_rejected')}}
            variant['review_digest']=review_digest(variant)
            if error_type:
                variant.update(error=error_type,passed=False,regression_passed=False)
            result['answer_results'].append(variant)
    result['review_digest']=review_digest(result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,default=Path(__file__).resolve().parents[1]/'bible_basic_questions.json')
    parser.add_argument('--profiles',nargs='+',choices=PROFILES,default=PROFILES)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--answers',action='store_true',help='Also compare unsafe offline drafts with checked claims; never use drafts in the app.')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    cases=json.loads(args.cases.read_text(encoding='utf-8'))
    if args.limit is not None:
        if args.limit<1: parser.error('limit must be positive')
        cases=cases[:args.limit]
    payload={'manifest':run_manifest(args.cases,ans.default_db),'results':[],'answer_results':[],'summary':{}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    try:
        for profile in args.profiles:
            for case in cases:
                try:
                    row=run_case(case,profile,args.answers)
                except Exception as error:
                    row={'id':case['id'],'profile':profile,'error':type(error).__name__,'passed':False}
                payload['answer_results'].extend(row.pop('answer_results',[]))
                payload['results'].append(row)
                args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                print(json.dumps({k:row.get(k) for k in ('id','profile','retrieval_hit','seconds','error')}),flush=True)
            rows=[r for r in payload['results'] if r['profile']==profile]
            measured=[r for r in rows if r.get('retrieval_hit') is not None]
            times=[r['seconds'] for r in rows if 'seconds' in r and 'error' not in r]
            payload['summary'][profile]={'cases':len(rows),'errors':sum('error' in r for r in rows),
                'retrieval_hits':sum(r['retrieval_hit'] is True for r in measured),'measured_retrieval_cases':len(measured),
                'median_seconds':statistics.median(times) if times else None,
                'failed_seconds_total':sum(r.get('seconds',0) for r in rows if 'error' in r),
                'llm_usage_totals':{key:sum(r.get('llm_usage',{}).get(key,0) for r in rows)
                                    for key in ('requests','input_tokens','output_tokens','token_reports')},
                'human_acceptance':'not_measured'}
        args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    finally:
        ans.default_db.client.close()
    return 1 if any('error' in r for r in payload['results']) else 0


if __name__=='__main__':
    raise SystemExit(main())
