"""Real-corpus regression checks; reference/keyword matches are diagnostics, not theological grading."""
import argparse
import json
from pathlib import Path
import re
import time
from services import ans
from loaddocs.scripture_references import parse_references
from services.evaluation_review import review_digest
from services.llm_usage import measure_llm_usage


def evaluate(case, answers=False, retrieved_rows=None):
    diagnostics = {}
    started = time.monotonic()
    scope={key:case[key] for key in ('edition_id','translation') if key in case}
    with measure_llm_usage() as usage:
        if retrieved_rows is not None:
            if answers:
                raise ValueError('Precomputed retrieval is only supported for retrieval diagnostics.')
            rows, answer = retrieved_rows, ''
        elif answers:
            answer, rows = ans.answer_question(case['question'], chat_history=case.get('chat_history'), document_type=case.get('document_type','scripture'), diagnostics=diagnostics, **scope)
        else:
            rows = ans.retrieve_ranked_contexts(case['question'], document_type=case.get('document_type','scripture'), top_k=ans.DEFAULT_TOP_K,
                                          candidate_limit=ans.DEFAULT_CANDIDATE_LIMIT, chat_history=case.get('chat_history'), diagnostics=diagnostics, **scope)
            answer = ''
    diagnostics['llm_usage'] = usage
    coordinates = [r for row in rows for r in json.loads(row['metadata'].get('references', '[]'))]
    groups=case.get('expected_reference_groups') or [case.get('expected_references', [])]
    group_hits=[]
    for group in groups:
        expected=[ref for value in group for ref in parse_references(value)]
        group_hits.append(any(ref.includes({'book_code':known.book,'chapter':known.chapter,'verse_label':known.start})
                         for ref in expected for value in coordinates for known in parse_references(value) if known.start))
    source_texts=case.get('expected_source_texts',[])
    if source_texts:
        normalized_rows=[' '.join(row['content'].split()) for row in rows]
        group_hits=[any(' '.join(text.split()) in content for content in normalized_rows) for text in source_texts]
    has_expected=bool(any(groups) or source_texts)
    hit=all(group_hits) if has_expected else False
    scope_ok=all(all(row['metadata'].get(key)==value for key,value in scope.items()) for row in rows)
    claims = diagnostics.get('grounding_evidence', [])
    meaning = ' '.join(claim['text'] for claim in claims) if claims else answer
    term_hits=sum(bool(re.search(term,meaning,re.I)) for term in case.get('answer_term_options',[]))
    abstained = bool(re.search(r'không.*(?:thông tin|bằng chứng|đủ|có trong)|chưa.*(?:xác định|xác nhận|cung cấp|đủ)|không\s+(?:được\s+)?(?:đề cập|nêu|cung cấp)', answer, re.I))
    if case.get('unanswerable'):
        passed = abstained and diagnostics.get('answer_validation') not in ('rejected','grounding_rejected') and not re.search(r'vượt dung lượng|vượt ngân sách',answer,re.I) if answers else None
    elif case.get('clarification'):
        passed = diagnostics.get('answer_validation') == 'clarification' if answers else None
    else:
        passed = hit and (not answers or ((diagnostics.get('answer_validation') in ('citation_and_quote_checked','source_qa_quote_checked') or
                         (case.get('verbatim',False) and diagnostics.get('retrieval_route') == 'exact_scripture' and answer == ans.render_scripture(rows)))
                         and all(re.search(term, meaning, re.I) for term in case.get('answer_terms', []))))
    if passed is not None:
        passed = passed and scope_ok and (not answers or (case.get('minimum_term_hits',0) <= term_hits <= case.get('maximum_term_hits',float('inf')) and all(not re.search(term,meaning,re.I) for term in case.get('forbidden_answer_terms',[]))))
        if case.get('expected_route'):
            passed = passed and diagnostics.get('retrieval_route')==case['expected_route']
    regression_passed = passed
    deterministic = bool(case.get('verbatim') and diagnostics.get('retrieval_route') == 'exact_scripture' and answer == ans.render_scripture(rows))
    result = {'id': case['id'], 'category':case.get('category','basic'), 'question': case['question'], 'retrieval_hit': hit if has_expected else None,
            'reference_group_hits':group_hits if has_expected else [], 'scope_preserved':scope_ok,
            'answer_option_hits':term_hits,
            'regression_passed': regression_passed,
            'passed': passed if not answers or deterministic or passed is False else None,
            'review_status': 'pending_human_review' if answers and not deterministic and passed is not False else 'automatic_check',
            'seconds': round(time.monotonic()-started, 2), 'answer': answer,
            'sources': [{'reference': r['metadata'].get('reference'), 'content': r['content'], 'metadata':r['metadata']} for r in rows],
            'diagnostics': {k: v for k, v in diagnostics.items() if k not in ('final_contexts','hybrid_top_contexts','rerank_top_contexts')}}
    result['review_digest'] = review_digest(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--answers', action='store_true')
    parser.add_argument('--case', action='append', help='Run selected fixture IDs; repeat for multiple cases.')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--cases', type=Path, help='Separate evaluation/holdout cases; default is the development regression set.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    cases = json.loads((args.cases or root/'bible_basic_questions.json').read_text(encoding='utf-8'))
    if args.case:
        if set(args.case) - {case['id'] for case in cases}:
            parser.error('Unknown case ID')
        cases = [case for case in cases if case['id'] in args.case]
    results = []
    try:
        for case in cases:
            try:
                result = evaluate(case, args.answers)
            except Exception as error:
                result = {'id':case['id'], 'question':case['question'], 'passed':False, 'error':f'{type(error).__name__}: {error}'}
            results.append(result)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k:v for k,v in result.items() if k in ('id','passed','retrieval_hit','seconds','error')}, ensure_ascii=False), flush=True)
        pending = sum(r.get('passed') is None for r in results)
        print(f"Accepted {sum(r.get('passed') is True for r in results)}; pending human review {pending}; inspect {args.output}.", flush=True)
        return 1 if any(r.get('passed') is False for r in results) else 2 if pending else 0
    finally:
        ans.default_db.client.close()


if __name__ == '__main__':
    raise SystemExit(main())
