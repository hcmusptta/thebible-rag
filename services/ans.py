import logging
import math
import json
import os
import re
import threading
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import torch
from sentence_transformers import CrossEncoder

from loaddocs.document_types import document_type_filter, source_details, source_location
from loaddocs.embedding import get_default_embedder
from loaddocs.scripture import extract_scripture_query_filters, normalize_scripture_text
from loaddocs.scripture_references import parse_references, Reference
from loaddocs.scripture_store import ScriptureStore
from loaddocs.scripture_data import SCRIPTURE_PROCESSING_VERSION
from services.model import LLMService, ContextBudgetError, LLMOutputError, LLMRequestError
from vectordb.vector_store import default_db


logger = logging.getLogger(__name__)
_RERANK_LOCK = threading.RLock()


class IncompleteScriptureReference(ValueError):
    """The indexed text does not cover every verse in the requested range."""


DEFAULT_TOP_K = max(1, int(os.getenv('RAG_TOP_K', '4')))
DEFAULT_CANDIDATE_LIMIT = max(
    DEFAULT_TOP_K,
    int(os.getenv('RAG_CANDIDATE_LIMIT', '20')),
)


@lru_cache(maxsize=1)
def get_llm_service() -> LLMService:
    return LLMService()


@lru_cache(maxsize=1)
def get_benchmark_llm_service() -> LLMService:
    return get_llm_service()


@lru_cache(maxsize=1)
def _load_reranker() -> CrossEncoder:
    model_name = os.getenv("RERANKER_MODEL_NAME", "AITeamVN/Vietnamese_Reranker")
    device = os.getenv("RERANKER_DEVICE", "auto")
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    reranker = CrossEncoder(model_name, device=device, max_length=512)
    scores = reranker.predict([("câu hỏi", "ngữ cảnh")])
    if len(scores) != 1:
        raise RuntimeError("Reranker không tạo được điểm số")
    logger.info("Loaded reranker %s on %s", model_name, device)
    return reranker


def _get_reranker() -> CrossEncoder:
    with _RERANK_LOCK:
        return _load_reranker()


def _clean_query_variant(value: str) -> str:
    value = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", str(value or ""))
    return " ".join(value.split()).strip()


def _select_context_content(item: Dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    child = str(item.get("content", "")).strip()
    parent = str(metadata.get("parent_content", "")).strip()
    return parent if parent and child and " ".join(child.split()) in " ".join(parent.split()) else child


def _resolve_scripture_followup(question: str, history: Optional[list]) -> str:
    if parse_references(question) or not re.search(r'\b(?:cau|doan|chuong) (?:do|nay|vua roi)\b',normalize_scripture_text(question)):
        return question
    previous = list(history or [])
    if previous and previous[-1].get('role') == 'user' and _clean_query_variant(previous[-1].get('content','')) == _clean_query_variant(question):
        previous.pop()
    prior_user = next((message.get('content','') for message in reversed(previous[-6:]) if message.get('role') == 'user'), '')
    references = parse_references(prior_user)
    if len(references)>1:
        raise IncompleteScriptureReference('Lượt trước có nhiều tham chiếu. Bạn muốn nói đến câu hoặc đoạn nào?')
    if references and re.search(r'\bcau (?:do|nay|vua roi)\b',normalize_scripture_text(question)):
        ref=references[0]
        if not ref.start or (ref.end and ref.end != ref.start) or (ref.end_chapter and ref.end_chapter != ref.chapter):
            raise IncompleteScriptureReference('Lượt trước là một chương hoặc khoảng câu. Bạn muốn nói đến câu nào?')
    return question + ' (' + references[0].display() + ')' if references else question


def _catechism_question_key(text: str) -> str:
    key = normalize_scripture_text(text)
    key = re.sub(r'^(?:theo|trong) (?:sach )?giao ly(?: hoi thanh cong giao)?\s*', '', key)
    return re.sub(r'^cac\s+', '', key) if key.endswith('la gi') else key


def _matching_prose_sources(question: str, filters: Optional[Dict[str, Any]]) -> list:
    wanted = _catechism_question_key(question)
    try:
        references = parse_references(question)
    except ValueError:
        references = []
    found = []
    parents = set()
    for row in default_db.get_all_documents():
        meta = row.get('metadata') or {}
        if meta.get('document_type') not in {'catechism','commentary'} or not default_db._metadata_match(meta, filters):
            continue
        text = meta.get('parent_content') or row.get('content','')
        match = re.match(r'^\s*\d{1,4}[.)]?\s+(.{1,600}?\?)', text, re.S)
        parent = meta.get('parent_id') or row['id']
        question_match = bool(wanted and meta.get('document_type') == 'catechism' and match and _catechism_question_key(match[1]) == wanted)
        try:
            known = parse_references(meta.get('scripture_references','')) if references else []
        except ValueError:
            known = []
        reference_match = any(
            ref.includes({'book_code':other.book,'chapter':other.chapter,'verse_label':other.start}) or
            (ref.start and other.includes({'book_code':ref.book,'chapter':ref.chapter,'verse_label':ref.start}))
            for ref in references for other in known
        )
        if (question_match or reference_match) and parent not in parents:
            parents.add(parent)
            found.append({**row,'source_question_match':question_match,'source_reference_match':reference_match})
    return found


def _collect_hybrid_candidates(
    question: str,
    chat_history: Optional[list] = None,
    candidate_limit: int = 15,
    is_benchmark: bool = False,
    filters: Optional[Dict[str, Any]] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
    retrieval_profile: str = 'full',
) -> Tuple[str, List[Dict[str, Any]]]:
    question = _clean_query_variant(question)
    if not question:
        return "", []

    history = list(chat_history or [])
    if (
        history
        and history[-1].get("role") == "user"
        and _clean_query_variant(history[-1].get("content", "")) == question
    ):
        history = history[:-1]
    canonical_question = default_db.normalize_query_names(question)
    planning_history = [
        {**message, 'content': default_db.normalize_query_names(message['content'])}
        if isinstance(message.get('content'), str) else message
        for message in history[-6:]
    ]
    plan = None
    if retrieval_profile == 'full':
        llm = get_benchmark_llm_service() if is_benchmark else get_llm_service()
        try:
            plan = llm.plan_retrieval(canonical_question, planning_history, default_db.get_search_vocabulary(canonical_question))
        except Exception:
            logger.exception("Query planning failed; retaining the original question")
    if not isinstance(plan, dict):
        plan = {"question": question, "queries": []}
    unnormalized_query = _clean_query_variant(plan.get("question", question)) or question
    query = default_db.normalize_query_names(unnormalized_query)
    predicate_probe = ''
    if retrieval_profile == 'full' and re.match(r'^ai\b', canonical_question, re.I):
        # The named object can be in a neighbouring verse, while the action identifies the answer.
        predicate = re.sub(r'\b(?:(?:chúa|đức|ông|bà|thánh)\s+)?[^\W\d_]+(?:-[^\W\d_]+)+\b', '', canonical_question, flags=re.I)
        if predicate != canonical_question:
            predicate_probe = re.sub(r'^ai\s+(?:là\s+)?', '', predicate, flags=re.I).strip(' ?.!,')
    variants = []
    seen = set()
    for value in [question, query, predicate_probe, *plan.get("queries", [])[:2]]:
        if not isinstance(value, str) or not value.strip():
            continue
        variant = default_db.normalize_query_names(_clean_query_variant(value))
        key = " ".join(re.sub(r"[^\w\s-]", "", variant.casefold()).split())
        if key not in seen:
            seen.add(key)
            variants.append(variant)
    variants = variants[:4]
    question_matches = (_matching_prose_sources(canonical_question, filters) or _matching_prose_sources(query, filters)) if retrieval_profile == 'full' else []
    merged = {item['id']:{**item,'query_fusion_score':1.0} for item in question_matches[:candidate_limit]}
    protected = list(merged)
    identity_name = re.fullmatch(r'\s*(?:(?:ông|ong|bà|ba|thánh|thanh)\s+)?(\w+(?:-\w+)+)\s+l[àa]\s+ai\s*[?.!]*\s*',query,re.I)
    reserve = max(1,min(4,candidate_limit // len(variants)))
    for variant in variants:
        if retrieval_profile == 'bm25':
            found = default_db.search_by_bm25(variant,candidate_limit,filters)
        else:
            vector = get_default_embedder().embed_query(variant)
            found = default_db.search_by_vector(vector,candidate_limit,filters) if retrieval_profile == 'vector' else default_db.hybrid_search(variant, vector, k=candidate_limit, filters=filters)
        for rank, item in enumerate(found, 1):
            candidate = merged.setdefault(item["id"], {**item, "query_fusion_score": 0.0})
            candidate["query_fusion_score"] += 1.0 / (60 + rank)
            if rank == 1 and identity_name and re.match(re.escape(identity_name[1])+r'\s+\w+(?:-\w+)+',variant,re.I):
                candidate['name_variant_leader'] = True
            if rank <= reserve and item['id'] not in protected:
                protected.append(item['id'])
    # Preserve the best hits of each probe; overlap must not crowd out a useful supplementary query.
    ordered = protected + [item['id'] for item in sorted(merged.values(),key=lambda item:item['query_fusion_score'],reverse=True) if item['id'] not in protected]
    candidates = [merged[key] for key in ordered[:candidate_limit]]
    if diagnostics is not None:
        diagnostics["retrieval_query"] = query
        diagnostics["query_normalized"] = canonical_question != question or query != unnormalized_query
        diagnostics["query_variant_count"] = len(variants)
        diagnostics["search_queries"] = variants
        diagnostics["filter_applied"] = bool(filters)
        diagnostics["filter_fallback_used"] = False
        diagnostics["hybrid_candidate_count"] = len(candidates)
        diagnostics['reserved_query_candidates'] = len(protected[:candidate_limit])
        diagnostics["_hybrid_contexts"] = [item.get("content", "") for item in candidates]
    return query, candidates


def _rerank_with_cross_encoder(
    question: str, candidates: List[Dict[str, Any]],
    diagnostics: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if not candidates:
        return []

    # A verse alone can omit the subject or relationship needed to answer the question.
    rerank_contexts = []
    for item in candidates:
        child = str(item.get("content", "")).strip()
        parent = _select_context_content(item)
        rerank_contexts.append(child + "\n\n" + parent if parent != child else child)
    if os.getenv("RERANK_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        if diagnostics is not None:
            diagnostics["reranker_enabled"] = False
            diagnostics["_reranked_contexts"] = rerank_contexts
        return candidates

    try:
        with _RERANK_LOCK:
            ranker = _get_reranker()
            tokenizer = ranker.tokenizer
            budget = (ranker.max_length or 512) - len(tokenizer.encode(question, add_special_tokens=False, verbose=False)) - tokenizer.num_special_tokens_to_add(pair=True)
            if budget < 16:
                raise ValueError('Câu hỏi quá dài cho reranker; hãy rút ngắn câu hỏi.')
            pairs, owners = [], []
            overflow = 0
            for owner, context in enumerate(rerank_contexts):
                tokens = tokenizer.encode(context, add_special_tokens=False, verbose=False)
                if len(tokens) <= budget:
                    pairs.append((question,context)); owners.append(owner)
                    continue
                overflow += 1
                start = 0
                while start < len(tokens):
                    end = min(start + budget, len(tokens))
                    while True:
                        window = tokenizer.decode(tokens[start:end], skip_special_tokens=True, clean_up_tokenization_spaces=False)
                        excess = len(tokenizer.encode(window, add_special_tokens=False, verbose=False)) - budget
                        if excess <= 0:
                            break
                        end -= max(1,excess)
                        if end <= start:
                            raise ValueError('Không thể tạo cửa sổ reranker hợp lệ.')
                    pairs.append((question,window)); owners.append(owner)
                    if end == len(tokens):
                        break
                    start = max(start+1, end-min(64,budget//4))
            window_scores = ranker.predict(
                pairs,
                batch_size=8,
                show_progress_bar=False,
                activation_fn=torch.nn.Sigmoid(),
            )
            if len(window_scores) != len(pairs) or not all(math.isfinite(float(s)) for s in window_scores):
                raise ValueError('Reranker trả điểm cửa sổ không hợp lệ.')
            # shortcut: max-window scoring can favor longer passages; recalibrate if corpus-level ranking regresses.
            scores = [max(float(score) for score,index in zip(window_scores,owners) if index==owner) for owner in range(len(candidates))]
            if diagnostics is not None:
                diagnostics.update(rerank_window_count=len(pairs), rerank_overflow_candidates=overflow,
                                   rerank_truncated_tokens=0, rerank_max_length=ranker.max_length or 512)
    except Exception:
        logger.exception("Reranker failed")
        raise RuntimeError("Reranker không hoạt động; chưa thể kiểm tra chất lượng context.") from None
    if len(scores) != len(candidates) or not all(math.isfinite(float(s)) for s in scores):
        raise RuntimeError("Reranker trả về điểm số không hợp lệ.")

    reranked = []
    for item, score in zip(candidates, scores):
        reranked.append({**item, "rerank_score": float(score)})
    reranked.sort(key=lambda item: (bool(item.get('source_question_match')),bool(item.get('source_reference_match')),item["rerank_score"]), reverse=True)
    if diagnostics is not None:
        diagnostics["reranker_enabled"] = True
        diagnostics["rerank_candidate_count"] = len(reranked)
        diagnostics["rerank_score_max"] = reranked[0]["rerank_score"] if reranked else None
        diagnostics["rerank_score_min"] = reranked[-1]["rerank_score"] if reranked else None
        diagnostics["_reranked_contexts"] = [
            item.get("content", "") for item in reranked
        ]
    return reranked


def retrieve_ranked_contexts(
    question: str,
    chat_history: Optional[list] = None,
    top_k: int = 4,
    candidate_limit: int = 20,
    is_benchmark: bool = False,
    diagnostics: Optional[Dict[str, Any]] = None,
    document_type: str | None = None,
    edition_id: str | None = None,
    translation: str | None = None,
    retrieval_profile: str = 'full',
) -> List[Dict[str, Any]]:
    if retrieval_profile not in {'bm25','vector','hybrid','hybrid_rerank','full'}:
        raise ValueError('Unknown retrieval profile.')
    if diagnostics is not None:
        diagnostics['retrieval_profile'] = retrieval_profile
    type_filters = document_type_filter(document_type)
    normalized = normalize_scripture_text(question)
    prose_question = re.search(r'\b(?:giao ly|catechism|chu giai|commentary)\b', normalized)
    is_scripture = document_type not in {"commentary", "catechism", "generic"} and not (
        document_type in {None, "all", "auto"} and prose_question
    )
    try:
        if is_scripture:
            resolved = _resolve_scripture_followup(question,chat_history)
            if diagnostics is not None and resolved != question:
                diagnostics['resolved_question'] = resolved
            question = resolved
        references = parse_references(question) if is_scripture else []
        normalized = normalize_scripture_text(question)
        term = re.search(r'["“]([^"”]+)["”]', question)
        count_intent = bool(re.search(r"\b(?:đếm|bao nhiêu)\b", question.casefold()) or re.search(r"\b(?:dem|bao nhieu)\b", question.casefold()))
        all_intent = bool(re.search(r"\b(?:tat ca|toan bo)\b", normalized))
        literal_intent = (term is not None and (count_intent or all_intent)) or bool(
            re.search(r'\bdem\b|\b(?:tat ca|toan bo) (?:cac )?(?:cau|doan|ban ghi)\b', normalized)
        )
        exhaustive = is_scripture and literal_intent and (not references or term is not None or count_intent)
        if exhaustive:
            results = ScriptureStore(default_db).exhaustive(term[1] if term else "", edition_id, translation, references)
        elif references:
            results = ScriptureStore(default_db).lookup(references, edition_id, translation)
        else:
            results = None
    except ValueError as error:
        raise IncompleteScriptureReference(str(error)) from error
    if results is not None:
        if diagnostics is not None:
            diagnostics.update(retrieval_route="exhaustive_scripture" if exhaustive else "exact_scripture",
                               references=[r.display() for r in references], returned_context_count=len(results),
                               final_contexts=[r["content"] for r in results])
        return results
    scripture_filters = (extract_scripture_query_filters(question) or type_filters) if is_scripture else type_filters
    clauses = []
    if scripture_filters:
        clauses.extend(scripture_filters.get("$and", [{k: v} for k, v in scripture_filters.items()]))
    if edition_id:
        clauses.append({"edition_id": edition_id})
    if translation:
        clauses.append({"translation": translation})
    # Old Scripture chunks stay archived, but cannot leak into the new answering flow.
    clauses.append({"$or": [
        {"document_type": {"$nin": ["scripture", "bible"]}},
        {"$and": [{"document_type": {"$in": ["scripture", "bible"]}}, {"processing_version": SCRIPTURE_PROCESSING_VERSION}]},
    ]})
    scripture_filters = {"$and": clauses} if len(clauses) > 1 else clauses[0]
    if diagnostics is not None:
        diagnostics["retrieval_route"] = "semantic"
    query, candidates = _collect_hybrid_candidates(
        question,
        chat_history,
        candidate_limit=candidate_limit,
        is_benchmark=is_benchmark,
        filters=scripture_filters or None,
        diagnostics=diagnostics,
        retrieval_profile=retrieval_profile,
    )
    if (retrieval_profile == 'full' and candidates and
        all((item.get('metadata') or {}).get('document_type') in {'scripture','bible','catechism','commentary'} for item in candidates) and
        any((item.get('metadata') or {}).get('document_type') in {'scripture','bible'} for item in candidates) and
        not any(item.get('source_question_match') for item in candidates)):
        llm = get_benchmark_llm_service() if is_benchmark else get_llm_service()
        reranked_candidates = llm.select_scripture_evidence(query, candidates, top_k,
                                      (diagnostics or {}).get('search_queries', [query]))
        if reranked_candidates is None:
            raise RuntimeError('Model chưa chọn được bằng chứng hợp lệ từ các loại tài liệu; vui lòng thử lại.')
        if reranked_candidates and re.fullmatch(r'\s*(?:(?:ông|ong|bà|ba|thánh|thanh)\s+)?\w+(?:-\w+)*\s+l[àa]\s+ai\s*[?.!]*\s*',query,re.I):
            # Keep alternate probes visible before deciding whether an unqualified name is ambiguous.
            leaders = [item for item in candidates if item.get('name_variant_leader')]
            combined = {item['id']:item for item in [*leaders,*reranked_candidates]}
            reranked_candidates = list(combined.values())[:top_k]
            if diagnostics is not None:
                diagnostics['identity_query_leaders_preserved'] = len(leaders[:top_k])
        if diagnostics is not None:
            diagnostics.update(evidence_selection='source_content', selected_candidate_count=len(reranked_candidates))
    elif retrieval_profile in {'hybrid_rerank','full'}:
        reranked_candidates = _rerank_with_cross_encoder(query, candidates, diagnostics=diagnostics)
    else:
        reranked_candidates = candidates
    ranked_parents = []
    seen_contexts = set()
    canonical_cache = {}
    for item in reranked_candidates:
        matched_content = item.get('content','')
        if (item.get("metadata") or {}).get("document_type") in {"scripture", "bible"}:
            try:
                item = ScriptureStore(default_db).expand(item, canonical_cache)
            except ValueError as error:
                raise IncompleteScriptureReference(str(error)) from error
            item["metadata"].pop("parent_content", None)
        rerank_score = item.get("rerank_score")
        metadata = dict(item.get("metadata") or {})
        parent_id = metadata.get("parent_id") or item.get("id")
        content = _select_context_content(item)
        context_key = (parent_id, content)
        if context_key in seen_contexts:
            existing = next(row for row in ranked_parents if (row['id'], row['content']) == context_key)
            existing['matched_contents'].append(matched_content)
            existing['matched_labels'].append(str(metadata.get('verse_label','')))
            continue
        if not content or len(ranked_parents) >= top_k:
            continue
        seen_contexts.add(context_key)
        if content == str(metadata.get("parent_content", "")).strip():
            for key in ("verse_start", "verse_end", "page", "page_end"):
                if metadata.get(f"parent_{key}") is not None:
                    metadata[key] = metadata[f"parent_{key}"]
        ranked_parents.append(
            {
                "id": parent_id,
                "content": content,
                "metadata": metadata,
                "rerank_score": rerank_score,
                "hybrid_score": float(item.get("hybrid_score", 0.0)),
                "vector_score": float(item.get("vector_score", 0.0)),
                "bm25_score": float(item.get("bm25_score", 0.0)),
                "vector_rank": item.get("vector_rank"),
                "bm25_rank": item.get("bm25_rank"),
                "child_id": item.get("id"),
                "child_content": matched_content,
                "matched_contents": [matched_content],
                "matched_labels": [str(metadata.get('verse_label',''))],
                "source_question_match": bool(item.get('source_question_match')),
            }
        )
    if diagnostics is not None:
        diagnostics["selected_candidate_count"] = len(ranked_parents)
        diagnostics["returned_context_count"] = len(ranked_parents)
        diagnostics["hybrid_top_contexts"] = diagnostics.pop("_hybrid_contexts", [])[:top_k]
        diagnostics["rerank_top_contexts"] = diagnostics.pop("_reranked_contexts", [])[:top_k]
        diagnostics["final_contexts"] = [item["content"] for item in ranked_parents]
        diagnostics["scripture_filters"] = scripture_filters
    if ranked_parents and not is_scripture and document_type in {None,'all','auto'}:
        try:
            requested = parse_references(question)
            canonical = ScriptureStore(default_db).lookup(requested, edition_id, translation) if requested else []
        except ValueError:
            canonical = []
        if canonical:
            ranked_parents = [ranked_parents[0], *canonical, *ranked_parents[1:]]
            if diagnostics is not None:
                diagnostics['canonical_reference_contexts'] = len(canonical)
                diagnostics['returned_context_count'] = len(ranked_parents)
                diagnostics['final_contexts'] = [row['content'] for row in ranked_parents]
    return ranked_parents


def _format_context(results: List[Dict[str, Any]]) -> str:
    blocks = []
    for index, result in enumerate(results, start=1):
        metadata = result.get("metadata") or {}
        source = metadata.get("source", "không rõ")
        location = [f"tài liệu: {source}", source_location(metadata)] + source_details(metadata)
        if metadata.get("reference"):
            location.append(f"tọa độ nguồn: {metadata['reference']}")
        if metadata.get("source_relations") and metadata["source_relations"] != "[]":
            location.append(f"quan hệ có bằng chứng nguồn (không tự suy ra tương đương): {metadata['source_relations']}")
        if metadata.get("annotations"):
            annotations = json.loads(metadata["annotations"])
            reviewed = {ref: value for ref, value in annotations.items() if value.get("origin") == "reviewed"}
            if reviewed:
                location.append(f"chú thích đã duyệt: {json.dumps(reviewed, ensure_ascii=False)}")
        if metadata.get("book"):
            location.append(f"sách: {metadata['book']}")
        if metadata.get("chapter") is not None:
            location.append(f"chương: {metadata['chapter']}")
        verse_start = metadata.get("verse_start")
        verse_end = metadata.get("verse_end")
        if verse_start is not None and not metadata.get("reference"):
            verse_label = str(verse_start)
            if verse_end is not None and verse_end != verse_start:
                verse_label = f"{verse_start}-{verse_end}"
            location.append(f"câu: {verse_label}")
        blocks.append(
            f"[Nguồn {index} | {' | '.join(location)}]\n"
            f"{result['content']}"
        )
    return "\n\n".join(blocks)


def answer_question(
    question: str,
    chat_history: Optional[list] = None,
    is_benchmark: bool = False,
    top_k: int = DEFAULT_TOP_K,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    diagnostics: Optional[Dict[str, Any]] = None,
    document_type: str | None = None,
    edition_id: str | None = None,
    translation: str | None = None,
):
    diagnostics = diagnostics if diagnostics is not None else {}
    try:
        top_results = retrieve_ranked_contexts(
            question,
            chat_history=chat_history,
            top_k=top_k,
            candidate_limit=candidate_limit,
            is_benchmark=is_benchmark,
            diagnostics=diagnostics,
            document_type=document_type,
            edition_id=edition_id, translation=translation,
        )
    except IncompleteScriptureReference as error:
        return str(error), []
    except (LLMOutputError, LLMRequestError) as error:
        diagnostics.update(answer_validation='provider_error', validation_reason=type(error).__name__)
        return str(error), []
    if not top_results:
        if diagnostics.get("retrieval_route") == "exhaustive_scripture":
            return "Có 0 bản ghi chứa cụm từ nguyên văn trong phạm vi đã nạp và được phép tra cứu; chưa thể khẳng định cho bản Kinh Thánh trọn bộ.", []
        return "Tôi không tìm thấy thông tin liên quan trong kho tài liệu.", []

    route = diagnostics.get("retrieval_route")
    question = diagnostics.get('resolved_question',question)
    normalized = normalize_scripture_text(question)
    if route == "exhaustive_scripture":
        complete = all(r["metadata"].get("corpus_complete") for r in top_results)
        coverage = "Phạm vi bản dịch đã duyệt." if complete else "Kết quả chỉ bao phủ dữ liệu đã nạp và được phép tra cứu; chưa xác nhận trọn bộ."
        if re.search(r"\b(?:dem|bao nhieu)\b", normalized):
            occurrences = sum(r["metadata"].get("literal_occurrences", 1) for r in top_results)
            editions = len({r["metadata"].get("edition_id") for r in top_results})
            return f"Có {len(top_results)} đoạn/câu nguồn chứa cụm từ nguyên văn, tổng {occurrences} lần xuất hiện trong {editions} bản đã chọn. Một câu gộp giữ nguyên nhãn nguồn. {coverage}", top_results
        return coverage + "\n\n" + render_scripture(top_results), top_results
    quoted = render_scripture(top_results) if route == "exact_scripture" else ""
    mandatory_count = len(top_results) if quoted else 1
    interpretation = bool(re.search(r"\b(?:giai thich|y nghia|so sanh|tai sao|phan tich|lien he|tom tat|neu|liet ke|ke ten|cho biet|ai|gi|nao|phai khong)\b", normalized))
    if quoted and not interpretation:
        return quoted, top_results
    source_answers = []
    for index, row in enumerate(top_results, 1):
        if not row.get('source_question_match') or (row.get('metadata') or {}).get('document_type') != 'catechism':
            continue
        tail = row['content'].partition('?')[2]
        if not any(not part.strip().endswith('?') and len(re.findall(r'\b[^\W\d_]+\b',part)) >= 3
                   for part in re.split(r'\n\s*\n',tail) if part.strip()):
            continue
        source_answers.append((index,row['content']))
    if source_answers:
        diagnostics['answer_validation'] = 'source_qa_quote_checked'
        diagnostics['grounding_evidence'] = [{'text':text,'quotes':[text],'sources':[index]} for index,text in source_answers]
        answer='\n\n'.join(f'**Trích nguyên văn mục hỏi–đáp trong tài liệu** [Nguồn {index}]\n\n'
                           + '\n'.join('> '+line for line in text.splitlines()) for index,text in source_answers)
        return answer,top_results
    if quoted and interpretation:
        for row in top_results:
            row['requested_source'] = True
        cache = {}
        additional = []
        introductions = []
        seen_books = set()
        for result in top_results:
            metadata=result['metadata']
            book=metadata.get('book_code')
            identity=(book,metadata.get('edition_id'))
            if not book or identity in seen_books or not re.search(r'\b(?:toi|chung toi)\b',normalize_scripture_text(result['content'])):
                continue
            seen_books.add(identity)
            try:
                opening=ScriptureStore(default_db).lookup([Reference(book,1,'1',end='3')],metadata.get('edition_id'),translation)
            except ValueError:
                continue
            text=normalize_scripture_text(' '.join(row['content'] for row in opening))
            if re.search(r'\b(?:gui|goi)\b',text) and re.search(r'\b(?:hoi thanh|anh em|kinh gui)\b',text):
                introductions.extend(row for row in opening if not any(row['content']==existing['content'] and row['metadata'].get('edition_id')==existing['metadata'].get('edition_id') for existing in top_results))
        for result in top_results:
            for passage_id in dict.fromkeys([result["metadata"]["passage_id"], result["metadata"]["last_passage_id"]]):
                expanded = ScriptureStore(default_db).expand({**result, "metadata": {**result["metadata"], "passage_id": passage_id}}, cache)
                if expanded["content"] != result["content"] and not any(r["content"] == expanded["content"] for r in additional):
                    additional.append(expanded)
        top_results = top_results + introductions + additional
    llm_service = get_benchmark_llm_service() if is_benchmark else get_llm_service()
    while True:
        try:
            context = _format_context(top_results)
            if len(context) > int(os.getenv('RAG_CONTEXT_MAX_CHARS','40000')):
                raise ContextBudgetError('Đoạn nguồn vượt dung lượng ngữ cảnh. Hãy chọn phạm vi nhỏ hơn để diễn giải; hệ thống giữ nguyên các đoạn nguồn.')
            if top_results:
                grounded = llm_service.generate_scripture_response(question, top_results,
                    resolved_question=diagnostics.get('retrieval_query') if route == 'semantic' else None)
                if grounded is None:
                    diagnostics['answer_validation'] = 'grounding_rejected'
                    return (quoted + '\n\n' if quoted else '') + 'Chưa thể cung cấp diễn giải có dẫn chứng hợp lệ. Bạn có thể xem bản văn ở các nguồn đính kèm.', top_results
                answer = grounded['answer']
                diagnostics['grounding_evidence'] = grounded['claims']
                diagnostics['validation_reason'] = grounded.get('validation_reason')
                if grounded.get('review_verdict'):
                    diagnostics['review_verdict'] = grounded['review_verdict']
                if grounded['clarify']:
                    diagnostics['answer_validation'] = 'clarification'
                    diagnostics['clarification_evidence'] = grounded.get('ambiguity_evidence',[])
                    return answer, top_results
            break
        except (LLMOutputError, LLMRequestError) as error:
            diagnostics['answer_validation'] = 'provider_error'
            diagnostics['validation_reason'] = type(error).__name__
            return (quoted + '\n\n' if quoted else '') + str(error), top_results
        except ContextBudgetError as error:
            if (route == 'semantic' and len(top_results) > 1) or (quoted and len(top_results) > mandatory_count):
                top_results = top_results[:-1]
                diagnostics['context_budget_removed_paragraphs'] = diagnostics.get('context_budget_removed_paragraphs',0) + 1
                diagnostics['returned_context_count'] = len(top_results)
                diagnostics['final_contexts'] = [row['content'] for row in top_results]
                continue
            return (quoted + "\n\n" if quoted else "") + str(error), top_results
    if answer.strip() == "KHONG_DU_NGUON":
        diagnostics["answer_validation"] = "insufficient_evidence"
        cause = diagnostics.get('validation_reason')
        if cause == 'answer_review_rejected':
            message = "Đã tìm thấy các đoạn liên quan, nhưng câu trả lời do model tạo chưa vượt qua bước kiểm chứng dẫn chứng. Bạn có thể xem nguyên văn ở các nguồn đính kèm."
        elif cause == 'source_generation_empty':
            message = "Đã tìm thấy các đoạn liên quan, nhưng model chưa tạo được câu trả lời có dẫn chứng cho câu hỏi này. Bạn có thể xem nguyên văn ở các nguồn đính kèm."
        else:
            message = "Nguồn hiện có chưa đủ bằng chứng để trả lời câu hỏi này."
        return (quoted + "\n\n" if quoted else "") + message, top_results
    if not citations_valid(answer, top_results):
        diagnostics["answer_validation"] = "rejected"
        return (quoted + "\n\n" if quoted else "") + "Chưa thể cung cấp diễn giải có dẫn chứng hợp lệ. Bạn có thể xem bản văn ở các nguồn đính kèm.", top_results
    diagnostics["answer_validation"] = "citation_and_quote_checked"
    return (quoted + "\n\nTrả lời:\n\n" if quoted else "") + answer, top_results


def render_scripture(results):
    return "\n\n".join(f"**{r['metadata'].get('reference', '')} — {r['metadata'].get('translation') or 'Bản dịch chưa khai báo'} ({r['metadata']['source']})** [Nguồn {i}]\n\n"
                        + (r['metadata']['scope_warning'] + '\n\n' if r['metadata'].get('scope_warning') else '')
                        + "\n".join("> " + line for line in r["content"].splitlines()) for i, r in enumerate(results, 1))


def citations_valid(answer, results):
    citations = [int(n) for n in re.findall(r"\[Nguồn (\d+)\]", answer)]
    if not citations or any(n < 1 or n > len(results) for n in citations):
        return False
    # shortcut: model grounding and quote checks do not prove theological entailment; use human review for interpretation.
    texts = [" ".join(r["content"].split()) for r in results]
    for quote in re.findall(r'["“]([^"”]+)["”]', answer):
        if not any(" ".join(quote.split()) in text for text in texts):
            return False
    try:
        refs = parse_references(answer)
    except ValueError:
        return False
    for ref in refs:
        known = []
        for result in results:
            known.extend(parse_references(r) for r in json.loads(result["metadata"].get("references", "[]")))
            if result["metadata"].get("document_type") in {"commentary", "catechism"}:
                known.append(parse_references(result["metadata"].get("scripture_references", "")))
        coordinates = [r for group in known for r in group]
        if ref.start:
            for chapter in range(ref.chapter, (ref.end_chapter or ref.chapter) + 1):
                available = [r for r in coordinates if r.book == ref.book and r.chapter == chapter and r.start
                             and ("+" in r.start) == ("+" in ref.start)]
                if not available:
                    return False
                lower = int(re.match(r"\d+", ref.start)[0]) if chapter == ref.chapter else 1
                upper = int(re.match(r"\d+", ref.end or ref.start)[0]) if chapter == (ref.end_chapter or ref.chapter) else max(int(re.match(r"\d+", r.end or r.start)[0]) for r in available)
                covered = {n for r in available for n in range(int(re.match(r"\d+", r.start)[0]), int(re.match(r"\d+", r.end or r.start)[0]) + 1)}
                if not set(range(lower, upper + 1)) <= covered:
                    return False
                for endpoint, endpoint_chapter in ((ref.start, ref.chapter), (ref.end or ref.start, ref.end_chapter or ref.chapter)):
                    if endpoint_chapter == chapter and re.search(r"[a-z+]", endpoint) and not any(endpoint in (r.start, r.end) for r in available):
                        return False
        elif not any(ref.book == r.book and r.chapter <= ref.chapter <= (r.end_chapter or r.chapter) for r in coordinates):
            return False
    return True
