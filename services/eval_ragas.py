import json
import logging
import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, TypeVar

from langchain_ollama import ChatOllama
from ragas import EvaluationDataset, evaluate
from loaddocs.embedding import TextEmbedder, get_default_embedder, get_embedder, model_key
from ragas.metrics import (
    AnswerRelevancy,
    AnswerSimilarity,
    ContextPrecision,
    FactualCorrectness,
    Faithfulness,
    LLMContextRecall,
    NonLLMContextPrecisionWithReference,
    NonLLMContextRecall,
)
from ragas.metrics._answer_relevance import ResponseRelevancePrompt
from ragas.run_config import RunConfig

from services.ans import answer_question, retrieve_ranked_contexts
from services.person_resolution import AmbiguousPersonError
from services.ragas_llm import SchemaConstrainedOllamaLLMWrapper
from vectordb.vector_store import default_db
from services.benchmark_manifest import run_manifest, validate_testset_sources
from services.llm_usage import measure_llm_usage

T = TypeVar("T")
logger = logging.getLogger(__name__)


class _EmptyContextScoreMixin:
    async def _single_turn_ascore(self, sample, callbacks):
        # A retrieval miss must contribute zero, not abort or disappear as NaN.
        if sample.retrieved_contexts == []:
            return 0.0
        return await super()._single_turn_ascore(sample, callbacks)


class _BenchmarkContextRecall(_EmptyContextScoreMixin, NonLLMContextRecall):
    pass


class _BenchmarkLLMContextRecall(_EmptyContextScoreMixin, LLMContextRecall):
    pass


class _BenchmarkFaithfulness(_EmptyContextScoreMixin, Faithfulness):
    pass


class _VietnameseAnswerRelevancePrompt(ResponseRelevancePrompt):
    instruction = """
Từ câu trả lời được cung cấp, hãy tạo đúng một câu hỏi tiếng Việt rõ ràng mà
câu trả lời đó trực tiếp giải đáp.

Yêu cầu bắt buộc:
- Trường `question` phải là một câu hỏi đầy đủ, có nội dung và không được rỗng.
- Không sao chép nguyên văn toàn bộ câu trả lời vào `question`.
- Đặt `noncommittal` là 1 nếu câu trả lời né tránh, mơ hồ hoặc nói không biết;
  ngược lại đặt là 0.
- Chỉ trả về đối tượng JSON đúng schema được yêu cầu, không thêm giải thích.
""".strip()


def _build_answer_relevancy_metric() -> AnswerRelevancy:
    metric = AnswerRelevancy(strictness=1)
    metric.question_generation = _VietnameseAnswerRelevancePrompt()
    return metric

DEFAULT_LIGHT_METRICS: List[str] = ["non_llm_context_recall"]

DEFAULT_FULL_METRICS: List[str] = [
    "llm_context_recall",
    "context_precision",
    "faithfulness",
    "factual_correctness",
    "answer_relevancy",
    "answer_similarity",
]

METRIC_REGISTRY: Dict[str, Dict[str, object]] = {
    "non_llm_context_recall": {
        "label": "non_llm_context_recall", "factory": _BenchmarkContextRecall,
        "requires_full": False, "requires_reference_contexts": True,
    },
    "non_llm_context_precision": {
        "label": "non_llm_context_precision", "factory": NonLLMContextPrecisionWithReference,
        "requires_full": False, "requires_reference_contexts": True,
    },
    "llm_context_recall": {
        "label": "llm_context_recall", "factory": _BenchmarkLLMContextRecall,
        "requires_full": True, "requires_llm": True, "requires_reference": True,
    },
    "context_precision": {
        "label": "context_precision", "factory": ContextPrecision,
        "requires_full": True, "requires_llm": True, "requires_reference": True,
    },
    "faithfulness": {
        "label": "faithfulness", "factory": _BenchmarkFaithfulness,
        "requires_full": True, "requires_llm": True, "requires_response": True,
    },
    "factual_correctness": {
        "label": "factual_correctness", "factory": FactualCorrectness,
        "requires_full": True, "requires_llm": True, "requires_response": True,
        "requires_reference": True,
    },
    "answer_relevancy": {
        "label": "answer_relevancy",
        "factory": _build_answer_relevancy_metric,
        "requires_full": True, "requires_llm": True, "requires_embeddings": True,
        "requires_response": True,
    },
    "answer_similarity": {
        "label": "answer_similarity", "factory": AnswerSimilarity,
        "requires_full": True, "requires_embeddings": True, "requires_response": True,
        "requires_reference": True,
    },
}

DEFAULT_SAMPLE_SIZE = int(os.getenv("RAGAS_SAMPLE_SIZE", "1"))
DEFAULT_TOP_K = int(os.getenv("RAGAS_TOP_K", os.getenv("RAG_TOP_K", "4")))
DEFAULT_CANDIDATE_LIMIT = int(os.getenv("RAGAS_CANDIDATE_LIMIT", "20"))
DEFAULT_TIMEOUT = int(os.getenv("RAGAS_TIMEOUT", "600"))


def _run_in_worker_thread(operation: Callable[[], T]) -> T:
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(operation).result()


def _retrieve_contexts_for_benchmark(
    question: str,
    top_k: int = DEFAULT_TOP_K,
    diagnostics: Optional[Dict[str, object]] = None,
    chat_history=None, document_type=None, edition_id=None, translation=None,
) -> List[str]:
    try:
        ranked_results = retrieve_ranked_contexts(
            question=question,
            chat_history=chat_history,
            top_k=top_k,
            candidate_limit=DEFAULT_CANDIDATE_LIMIT,
            is_benchmark=True,
            diagnostics=diagnostics,
            document_type=document_type, edition_id=edition_id, translation=translation,
        )
    except AmbiguousPersonError:
        return []
    return [item["content"] for item in ranked_results]


def _build_ragas_embeddings() -> TextEmbedder:
    model_name = os.getenv("RAGAS_EMBEDDING_MODEL") or os.getenv(
        "EMBEDDING_MODEL_NAME",
        "AITeamVN/Vietnamese_Embedding_v2",
    )
    app_model = os.getenv("EMBEDDING_MODEL_NAME") or "AITeamVN/Vietnamese_Embedding_v2"
    if model_key(model_name) == model_key(app_model):
        return get_default_embedder()
    # A separate evaluator embedding model defaults to CPU to preserve GPU capacity.
    return get_embedder(model_name, device=os.getenv("RAGAS_EMBEDDING_DEVICE", "cpu"))


def _build_local_llm():
    model_name = os.getenv("RAGAS_JUDGE_MODEL", "qwen3.5:9b")
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    max_tokens = int(os.getenv("RAGAS_JUDGE_MAX_TOKENS", "2048"))
    if max_tokens <= 0:
        raise ValueError("RAGAS_JUDGE_MAX_TOKENS must be positive.")
    return SchemaConstrainedOllamaLLMWrapper(
        ChatOllama(
            model=model_name,
            base_url=base_url,
            temperature=float(os.getenv("RAGAS_JUDGE_TEMPERATURE", "0")),
            top_p=0.8,
            top_k=20,
            num_predict=max_tokens,
            format="json",
            reasoning=False,
            num_ctx=int(os.getenv("RAGAS_JUDGE_NUM_CTX", "8192")),
        ),
        bypass_temperature=True,
    )


def _build_run_config() -> RunConfig:
    return RunConfig(
        timeout=DEFAULT_TIMEOUT,
        max_workers=1,
        max_retries=3,
        max_wait=10,
    )

def get_available_metric_options(full_metrics: bool = False) -> List[Dict[str, str]]:
    """
    Trả về danh sách các metric khả dụng dựa trên chế độ chạy (Light/Full)
    để hiển thị trên giao diện.
    """
    metric_keys = DEFAULT_FULL_METRICS if full_metrics else DEFAULT_LIGHT_METRICS
    return [
        {"label": METRIC_REGISTRY[key]["label"], "value": key}
        for key in metric_keys
        if key in METRIC_REGISTRY
    ]


def _resolve_metric_keys(metric_keys: Optional[List[str]], full_metrics: bool) -> List[str]:
    if metric_keys is not None:
        return metric_keys
    return DEFAULT_FULL_METRICS if full_metrics else DEFAULT_LIGHT_METRICS


def _requires(metric_keys: List[str], flag: str) -> bool:
    return any(bool(METRIC_REGISTRY.get(key, {}).get(flag, False)) for key in metric_keys)


def _resolve_sample_size(sample_size: Optional[int]) -> int:
    if sample_size is None:
        return DEFAULT_SAMPLE_SIZE
    return max(1, sample_size)

def _build_metrics(metric_keys: List[str], full_metrics: bool):
    metrics = []
    invalid_keys = []
    blocked_keys = []

    for key in metric_keys:
        definition = METRIC_REGISTRY.get(key)

        if not definition:
            invalid_keys.append(key)
            continue

        if definition["requires_full"] and not full_metrics:
            blocked_keys.append(key)
            continue

        metric = definition["factory"]()
        metric.name = key
        metrics.append(metric)

    if invalid_keys:
        raise ValueError(f"Metric không tồn tại trong METRIC_REGISTRY: {invalid_keys}")

    if blocked_keys:
        raise ValueError(
            f"Các metric này cần bật full_metrics=True: {blocked_keys}"
        )

    return metrics


def _load_golden_testset(testset_path: str) -> List[Dict[str, object]]:
    with open(testset_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Golden testset phải là một mảng JSON.")
    return payload


def _resolve_golden_testset_path() -> str:
    env_path = os.getenv("GOLDEN_TESTSET_PATH")
    candidate_paths = [
        env_path,
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "golden_testset.json")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "golden_testset.json")),
    ]

    for candidate in candidate_paths:
        if candidate and os.path.exists(candidate):
            return candidate

    return candidate_paths[1]

def _normalize_text(text: str) -> str:
    return " ".join(str(text).lower().split())


def _quick_context_hit_check(
    reference_contexts: List[str],
    retrieved_contexts: List[str],
    min_chars: int = 80,
) -> bool:
    if not reference_contexts or not retrieved_contexts:
        return False

    normalized_retrieved = [_normalize_text(ctx) for ctx in retrieved_contexts]

    for reference_context in reference_contexts:
        normalized_reference = _normalize_text(reference_context)

        if not normalized_reference:
            continue

        probe = normalized_reference[:min_chars]

        if any(probe in ctx for ctx in normalized_retrieved):
            return True

    return False


def _summarize_retrieval_diagnostics(
    report_rows: List[Dict[str, object]],
) -> Dict[str, float]:
    diagnostics_rows = [
        row.get("retrieval_diagnostics") or {}
        for row in report_rows
    ]
    keys = {
        "hybrid_candidates_avg": "hybrid_candidate_count",
        "rerank_candidates_avg": "rerank_candidate_count",
        "selected_candidates_avg": "selected_candidate_count",
        "contexts_returned_avg": "returned_context_count",
        "hybrid_exact_hit_at_k": "hybrid_exact_hit_at_k",
        "rerank_exact_hit_at_k": "rerank_exact_hit_at_k",
        "final_exact_hit_at_k": "final_exact_hit_at_k",
    }
    summary: Dict[str, float] = {}
    for output_key, source_key in keys.items():
        values = [
            float(row[source_key])
            for row in diagnostics_rows
            if source_key in row and row[source_key] is not None
        ]
        if values:
            summary[output_key] = sum(values) / len(values)
    return summary


def run_ragas_benchmark(
    sample_size: Optional[int] = None,
    full_metrics: bool = False,
    metric_keys: Optional[List[str]] = None,
):
    if default_db.count() == 0:
        raise ValueError("Kho dữ liệu đang trống, chưa thể chạy Ragas.")

    testset_path = _resolve_golden_testset_path()
    if not os.path.exists(testset_path):
        raise ValueError(f"Không tìm thấy golden testset tại: {testset_path}")

    golden_rows = _load_golden_testset(testset_path)
    if not golden_rows:
        raise ValueError("Golden testset đang rỗng, chưa thể chạy Ragas.")

    selected_metric_keys = _resolve_metric_keys(metric_keys, full_metrics)
    if not selected_metric_keys:
        raise ValueError("Vui lòng chọn ít nhất một metric RAGAS.")
    needs_llm = _requires(selected_metric_keys, "requires_llm")
    needs_embeddings = _requires(selected_metric_keys, "requires_embeddings")
    needs_response = _requires(selected_metric_keys, "requires_response")
    needs_reference = _requires(selected_metric_keys, "requires_reference")
    needs_reference_contexts = _requires(
        selected_metric_keys, "requires_reference_contexts"
    )

    dataset_rows = []
    report_rows = []
    preparation_errors = []
    resolved_sample_size = _resolve_sample_size(sample_size)
    limited_rows = golden_rows[:resolved_sample_size]
    manifest = run_manifest(testset_path, default_db)
    manifest['evaluation'] = {'metrics': selected_metric_keys, 'sample_size': len(limited_rows),
                              'top_k': DEFAULT_TOP_K, 'candidate_limit': DEFAULT_CANDIDATE_LIMIT}
    source_validation = validate_testset_sources(limited_rows, manifest['source_catalog'])
    manifest['testset_source_validation'] = source_validation
    if source_validation['mismatches']:
        ids = ', '.join(str(item['id']) for item in source_validation['mismatches'])
        raise ValueError('Testset tham chiếu nguồn/phiên bản khác kho hiện tại: ' + ids + '. Hãy đối chiếu lại testset; không tự sửa metadata để vượt kiểm tra.')

    for idx, row in enumerate(limited_rows):
        try:
            question = str(row.get("user_input", "")).strip()
            reference = str(row.get("reference", "")).strip()
            reference_contexts = row.get("reference_contexts") or []
            metadata = row.get("metadata") or {}

            if isinstance(reference_contexts, str):
                reference_contexts = [reference_contexts]

            reference_contexts = [
                str(ctx).strip()
                for ctx in reference_contexts
                if str(ctx).strip()
            ]

            if not question:
                raise ValueError("thiếu user_input")

            if needs_reference and not reference:
                raise ValueError("thiếu reference")

            if needs_reference_contexts and not reference_contexts:
                raise ValueError("thiếu reference_contexts")
            retrieval_diagnostics: Dict[str, object] = {}

            started = time.monotonic()
            with measure_llm_usage() as usage:
                if needs_response:
                    predicted_answer, retrieved_items = answer_question(
                        question, row.get('chat_history'), is_benchmark=True,
                        top_k=DEFAULT_TOP_K, candidate_limit=DEFAULT_CANDIDATE_LIMIT,
                        diagnostics=retrieval_diagnostics,
                        document_type=row.get('document_type'), edition_id=row.get('edition_id'),
                        translation=row.get('translation'),
                    )
                    retrieved_contexts = [
                        str(item.get("content", "")).strip() for item in retrieved_items
                        if str(item.get("content", "")).strip()
                    ]
                else:
                    retrieved_contexts = _retrieve_contexts_for_benchmark(
                        question, diagnostics=retrieval_diagnostics,
                        chat_history=row.get('chat_history'), document_type=row.get('document_type'),
                        edition_id=row.get('edition_id'), translation=row.get('translation'),
                    )
                    predicted_answer = ""
            if retrieval_diagnostics.get('answer_validation') == 'provider_error':
                raise RuntimeError('Provider trả lời bị lỗi; không dùng thông báo lỗi làm đáp án benchmark.')
            retrieval_diagnostics['llm_usage'] = usage
            retrieval_diagnostics['seconds'] = time.monotonic() - started
            retrieved_contexts = [
                str(ctx).strip() for ctx in retrieved_contexts if str(ctx).strip()
            ]
            retrieval_diagnostics["returned_context_count"] = len(retrieved_contexts)
            if not retrieved_contexts:
                logger.warning(
                    "Mẫu %s: không retrieve được context; vẫn giữ trong benchmark. "
                    "route=%s, filters=%s, hybrid_candidates=%s, "
                    "rerank_score_max=%s",
                    row.get("id", idx + 1),
                    retrieval_diagnostics.get("retrieval_route"),
                    retrieval_diagnostics.get("scripture_filters"),
                    retrieval_diagnostics.get("hybrid_candidate_count"),
                    retrieval_diagnostics.get("rerank_score_max"),
                )
            is_retrieved = _quick_context_hit_check(
                reference_contexts=reference_contexts,
                retrieved_contexts=retrieved_contexts
            )
            stage_context_keys = {
                "hybrid": "hybrid_top_contexts",
                "rerank": "rerank_top_contexts",
                "final": "final_contexts",
            }
            for stage, contexts_key in stage_context_keys.items():
                stage_contexts = retrieval_diagnostics.pop(contexts_key, [])
                retrieval_diagnostics[f"{stage}_exact_hit_at_k"] = (
                    _quick_context_hit_check(reference_contexts, stage_contexts)
                )
            logger.info("[%s] Đã thêm câu hỏi: %s", "TRÚNG" if is_retrieved else "TRƯỢT", question)

            dataset_rows.append({
                "user_input": question,
                "retrieved_contexts": retrieved_contexts,
                "reference_contexts": reference_contexts,
                "response": predicted_answer,
                "reference": reference,
            })
            report_rows.append({
                "user_input": question,
                "retrieved_contexts": retrieved_contexts,
                "reference_contexts": reference_contexts,
                "response": predicted_answer,
                "reference": reference,
                "source": metadata.get("source", ""),
                "page": metadata.get("page", ""),
                "id": row.get("id", ""),
                "topic": metadata.get("topic", ""),
                "difficulty": metadata.get("difficulty", ""),
                "retrieval_diagnostics": retrieval_diagnostics,
            })
        except Exception as e:
            preparation_errors.append(
                f"Mẫu {row.get('id', idx + 1)}: {str(e)}"
            )

    if preparation_errors:
        raise RuntimeError(
            "Benchmark dừng vì có mẫu không hợp lệ:\n- "
            + "\n- ".join(preparation_errors)
        )

    ragas_dataset = EvaluationDataset.from_list(dataset_rows)

    metrics = _build_metrics(selected_metric_keys, full_metrics)
    if not metrics:
        raise ValueError("Không có chỉ số Ragas hợp lệ để chạy.")

    evaluation_run_config = _build_run_config()

    def _run_evaluate():
        evaluation_kwargs = {
            "dataset": ragas_dataset,
            "metrics": metrics,
            "run_config": evaluation_run_config,
            "allow_nest_asyncio": False,
            "raise_exceptions": True,
        }
        if needs_llm:
            evaluation_kwargs["llm"] = _build_local_llm()
        if needs_embeddings:
            evaluation_kwargs["embeddings"] = _build_ragas_embeddings()
        return evaluate(
            **evaluation_kwargs,
        )

    # Individual judge requests retry through RunConfig; never restart all metrics.
    evaluation_result = _run_in_worker_thread(_run_evaluate)

    if hasattr(evaluation_result, "to_pandas"):
        result_frame = evaluation_result.to_pandas().rename(columns={
            f"{metric.name}(mode={metric.mode})": metric.name
            for metric in metrics
            if isinstance(metric, FactualCorrectness)
        })
        summary = {
            key: float(result_frame[key].mean())
            for key in selected_metric_keys
            if key in result_frame.columns
        }
    elif isinstance(evaluation_result, dict):
        summary = {
            key: float(evaluation_result[key])
            for key in selected_metric_keys
            if isinstance(evaluation_result.get(key), (int, float))
        }
        result_frame = None
    else:
        summary = {}
        result_frame = None

    return {
        "manifest": manifest,
        "summary": summary,
        "rows": report_rows,
        "details": result_frame,
        "retrieval_summary": _summarize_retrieval_diagnostics(report_rows),
        "mode": "full" if full_metrics else "light",
        "metrics": selected_metric_keys,
    }
