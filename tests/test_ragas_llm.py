import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.outputs import Generation, LLMResult
from langchain_core.prompt_values import StringPromptValue
from ragas.metrics._faithfulness import NLIStatementInput, NLIStatementPrompt
from ragas.prompt.pydantic_prompt import fix_output_format_prompt
from ragas.run_config import RunConfig

from services.ragas_llm import (
    RagasJudgeOutputError,
    SchemaConstrainedOllamaLLMWrapper,
    extract_ragas_output_schema,
)


class _FakeOllamaModel:
    def __init__(self, *, output_format="json", calls=None, outputs=None,
                 reasoning=True, temperature=0.5):
        self.format = output_format
        self.calls = calls if calls is not None else []
        self.outputs = outputs if outputs is not None else []
        self.reasoning = reasoning
        self.temperature = temperature

    def model_copy(self, *, update):
        return _FakeOllamaModel(
            output_format=update.get("format", self.format),
            calls=self.calls,
            outputs=self.outputs,
            reasoning=update.get("reasoning", self.reasoning),
            temperature=update.get("temperature", self.temperature),
        )

    def generate_prompt(self, prompts, **kwargs):
        self.calls.append({
            "format": self.format, "reasoning": self.reasoning,
            "temperature": self.temperature, "prompts": prompts, **kwargs,
        })
        text = self.outputs.pop(0) if self.outputs else '{"value": 1}'
        return LLMResult(
            generations=[[Generation(text=text)] for _ in prompts]
        )

    async def agenerate_prompt(self, prompts, **kwargs):
        return self.generate_prompt(prompts, **kwargs)


def _ragas_prompt(schema):
    return StringPromptValue(
        text=(
            "Return JSON.\n"
            "Please return the output in a JSON format that complies with the "
            "following schema as specified in JSON Schema:\n"
            f"{schema}\nDo not use single quotes.\nOutput: "
        )
    )


class RagasOutputSchemaTests(unittest.TestCase):
    def test_extracts_schema_without_consuming_trailing_prompt_text(self):
        prompt = _ragas_prompt(
            '{"type":"object","properties":{"value":{"type":"integer"}}}'
        )

        self.assertEqual(
            {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
            },
            extract_ragas_output_schema(prompt),
        )

    def test_returns_none_when_prompt_has_no_ragas_schema(self):
        self.assertIsNone(
            extract_ragas_output_schema(StringPromptValue(text="ordinary prompt"))
        )

    def test_sync_generation_uses_schema_and_preserves_original_model(self):
        model = _FakeOllamaModel()
        wrapper = SchemaConstrainedOllamaLLMWrapper(
            model, bypass_temperature=True
        )

        result = wrapper.generate_text(
            _ragas_prompt('{"type":"object"}'), n=2
        )

        self.assertEqual({"type": "object"}, model.calls[0]["format"])
        self.assertFalse(model.calls[0]["reasoning"])
        self.assertFalse(model.calls[0]["stream"])
        self.assertEqual(0.5, model.calls[0]["temperature"])
        self.assertEqual("system", model.calls[0]["prompts"][0].messages[0].type)
        self.assertEqual("json", model.format)
        self.assertTrue(model.reasoning)
        self.assertEqual(2, len(result.generations[0]))

    def test_async_generation_uses_schema(self):
        model = _FakeOllamaModel()
        wrapper = SchemaConstrainedOllamaLLMWrapper(
            model, bypass_temperature=True
        )

        result = asyncio.run(
            wrapper.agenerate_text(_ragas_prompt('{"type":"object"}'), n=2)
        )

        self.assertEqual({"type": "object"}, model.calls[0]["format"])
        self.assertEqual(2, len(result.generations[0]))


class RagasJudgeValidationTests(unittest.TestCase):
    def test_real_faithfulness_prompt_retries_only_bad_output_without_json_repair(self):
        prompt = NLIStatementPrompt()
        data = NLIStatementInput(
            context="Saul regained sight.", statements=["Saul regained sight."],
        )
        expected = {"statements": [{
            "statement": data.statements[0], "reason": "Explicitly stated.",
            "verdict": 1,
        }]}
        for invalid in (
            "The context states that Saul regained sight. Output: [1, 0, 0].",
            json.dumps({"text": "The context states that Saul regained sight."}),
            json.dumps({"statements": [{"statement": "Saul regained sight."}]}),
        ):
            with self.subTest(invalid=invalid):
                model = _FakeOllamaModel(outputs=[invalid, json.dumps(expected)])
                wrapper = SchemaConstrainedOllamaLLMWrapper(
                    model, run_config=RunConfig(max_retries=2, max_wait=0),
                    bypass_temperature=True,
                )
                with (
                    patch.object(fix_output_format_prompt, "generate", new_callable=AsyncMock) as repair,
                    self.assertLogs("services.ragas_llm", level="WARNING"),
                ):
                    result = asyncio.run(prompt.generate(llm=wrapper, data=data))
                self.assertEqual(expected, result.model_dump())
                self.assertEqual(2, len(model.calls))
                repair.assert_not_called()
                for call in model.calls:
                    self.assertEqual(prompt.output_model.model_json_schema(), call["format"])
                    self.assertFalse(call["reasoning"])

    def test_permanent_invalid_output_stops_after_request_retry_budget(self):
        model = _FakeOllamaModel(outputs=["not JSON"] * 2)
        wrapper = SchemaConstrainedOllamaLLMWrapper(
            model, run_config=RunConfig(max_retries=2, max_wait=0),
            bypass_temperature=True,
        )
        with (
            self.assertLogs("services.ragas_llm", level="WARNING"),
            self.assertRaisesRegex(RagasJudgeOutputError, "No score was computed"),
        ):
            asyncio.run(wrapper.generate(_ragas_prompt('{"type":"object"}')))
        self.assertEqual(2, len(model.calls))

    def test_real_ollama_client_receives_schema_and_no_thinking(self):
        prompt = NLIStatementPrompt()
        schema = prompt.output_model.model_json_schema()
        data = NLIStatementInput(context="Example context.", statements=["Example."])
        import os
        from services import eval_ragas

        with patch.dict(os.environ, {
            "RAGAS_JUDGE_MODEL": "test-model",
            "RAGAS_JUDGE_MAX_TOKENS": "2048",
            "RAGAS_JUDGE_TEMPERATURE": "0.7",
        }):
            wrapper = eval_ragas._build_local_llm()
        model = wrapper.langchain_llm
        response = {
            "model": "test-model", "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps({
                "statements": [{"statement": "Example.", "reason": "No support.", "verdict": 0}],
            })},
        }
        with patch.object(model._async_client, "chat", new_callable=AsyncMock, return_value=response) as chat:
            asyncio.run(prompt.generate(llm=wrapper, data=data))
        request = chat.call_args.kwargs
        self.assertEqual(schema, request["format"])
        self.assertFalse(request["think"])
        self.assertFalse(request["stream"])
        self.assertEqual("system", request["messages"][0]["role"])
        self.assertEqual(2048, request["options"]["num_predict"])
        self.assertEqual(0.7, request["options"]["temperature"])
        self.assertEqual(0.8, request["options"]["top_p"])
        self.assertEqual(20, request["options"]["top_k"])

    def test_truncated_output_is_rejected_even_if_json_parses(self):
        result = LLMResult(generations=[[Generation(
            text='{"value": 1}',
            generation_info={"done_reason": "length", "eval_count": 2048},
        )]])
        with (
            self.assertLogs("services.ragas_llm", level="WARNING"),
            self.assertRaisesRegex(RagasJudgeOutputError, "output token limit"),
        ):
            SchemaConstrainedOllamaLLMWrapper._validate_generations(
                result, {"type": "object"}, 1,
            )

    def test_judge_rejects_unlimited_output_configuration(self):
        import os
        from services import eval_ragas

        with (
            patch.dict(os.environ, {"RAGAS_JUDGE_MAX_TOKENS": "-1"}),
            self.assertRaisesRegex(ValueError, "must be positive"),
        ):
            eval_ragas._build_local_llm()

    def test_missing_schema_stops_before_calling_model(self):
        model = _FakeOllamaModel()
        wrapper = SchemaConstrainedOllamaLLMWrapper(model, bypass_temperature=True)
        with self.assertRaisesRegex(RagasJudgeOutputError, "no readable output schema"):
            wrapper.generate_text(StringPromptValue(text="Unknown prompt format"))
        self.assertEqual([], model.calls)


class _BenchmarkTestCase(unittest.TestCase):
    def setUp(self):
        catalog = patch('services.eval_ragas.default_db.get_source_catalog', return_value={})
        catalog.start()
        self.addCleanup(catalog.stop)


class RagasBenchmarkRetryTests(_BenchmarkTestCase):
    def test_failed_evaluation_does_not_restart_all_metrics(self):
        from services import eval_ragas

        row = {
            "user_input": "Question?", "reference": "Reference.",
            "reference_contexts": ["Context."],
        }
        with (
            patch("builtins.print"),
            patch.object(eval_ragas.default_db, "count", return_value=1),
            patch.object(eval_ragas, "_resolve_golden_testset_path", return_value=__file__),
            patch.object(eval_ragas, "_load_golden_testset", return_value=[row]),
            patch.object(eval_ragas, "answer_question", return_value=("Answer.", [{"content": "Context."}])),
            patch.object(eval_ragas, "_build_local_llm"),
            patch.object(eval_ragas, "evaluate", side_effect=RagasJudgeOutputError("bad judge output")) as evaluate,
            self.assertRaisesRegex(RagasJudgeOutputError, "bad judge output"),
        ):
            eval_ragas.run_ragas_benchmark(sample_size=1, full_metrics=True, metric_keys=["faithfulness"])
        evaluate.assert_called_once()


class RagasBenchmarkReportTests(_BenchmarkTestCase):
    def test_report_includes_factual_correctness_with_ragas_mode_suffix(self):
        from types import SimpleNamespace
        from pandas import DataFrame
        from services import eval_ragas

        row = {
            "user_input": "Question?", "reference": "Reference.",
            "response": "Answer.", "retrieved_contexts": ["Context."],
        }
        evaluation_result = SimpleNamespace(
            to_pandas=lambda: DataFrame({"factual_correctness(mode=f1)": [0.75]}),
        )
        with (
            patch.object(eval_ragas.default_db, "count", return_value=1),
            patch.object(eval_ragas, "_resolve_golden_testset_path", return_value=__file__),
            patch.object(eval_ragas, "_load_golden_testset", return_value=[row]),
            patch.object(eval_ragas, "answer_question", return_value=("Answer.", [{"content": "Context."}])),
            patch.object(eval_ragas, "_build_local_llm"),
            patch.object(eval_ragas, "evaluate", return_value=evaluation_result),
        ):
            report = eval_ragas.run_ragas_benchmark(
                sample_size=1, full_metrics=True, metric_keys=["factual_correctness"],
            )
        self.assertEqual({"factual_correctness": 0.75}, report["summary"])
        self.assertEqual([0.75], report["details"]["factual_correctness"].tolist())


class RagasBenchmarkEmptyContextTests(_BenchmarkTestCase):
    def test_light_benchmark_keeps_misses_in_scores_and_report(self):
        from services import eval_ragas

        rows = [
            {"id": sample_id, "user_input": "Question?", "reference_contexts": ["Context."]}
            for sample_id in ("kt_004", "kt_007")
        ]
        for contexts, scores in (
            ([["Context."], []], [1.0, 0.0]),
            ([[], ["   "]], [0.0, 0.0]),
        ):
            with (
                self.subTest(contexts=contexts),
                patch.object(eval_ragas.default_db, "count", return_value=1),
                patch.object(eval_ragas, "_resolve_golden_testset_path", return_value=__file__),
                patch.object(eval_ragas, "_load_golden_testset", return_value=rows),
                patch.object(eval_ragas, "_retrieve_contexts_for_benchmark", side_effect=contexts),
                patch.object(eval_ragas, "_build_local_llm") as judge,
                patch.object(eval_ragas, "_build_ragas_embeddings") as embeddings,
                self.assertLogs("services.eval_ragas", level="WARNING"),
            ):
                report = eval_ragas.run_ragas_benchmark(sample_size=2)
                self.assertEqual(sum(scores) / 2, report["summary"]["non_llm_context_recall"])
                self.assertEqual(scores, report["details"]["non_llm_context_recall"].tolist())
                self.assertEqual(["kt_004", "kt_007"], [row["id"] for row in report["rows"]])
                self.assertEqual([], report["rows"][1]["retrieved_contexts"])
                self.assertEqual(sum(scores) / 2, report["retrieval_summary"]["contexts_returned_avg"])
                judge.assert_not_called()
                embeddings.assert_not_called()

    def test_full_context_metrics_score_empty_context_without_judge_calls(self):
        from services import eval_ragas

        row = {
            "id": "kt_004", "user_input": "Question?", "reference": "Reference.",
            "reference_contexts": ["Context."],
        }
        model = _FakeOllamaModel()
        metric_keys = [
            "non_llm_context_precision", "llm_context_recall",
            "context_precision", "faithfulness",
        ]
        with (
            patch.object(eval_ragas.default_db, "count", return_value=1),
            patch.object(eval_ragas, "_resolve_golden_testset_path", return_value=__file__),
            patch.object(eval_ragas, "_load_golden_testset", return_value=[row]),
            patch.object(eval_ragas, "answer_question", return_value=("No information found.", [])),
            patch.object(
                eval_ragas, "_build_local_llm",
                return_value=SchemaConstrainedOllamaLLMWrapper(model, bypass_temperature=True),
            ),
            self.assertLogs("services.eval_ragas", level="WARNING"),
        ):
            report = eval_ragas.run_ragas_benchmark(
                sample_size=1, full_metrics=True, metric_keys=metric_keys,
            )
        self.assertEqual({key: 0.0 for key in metric_keys}, report["summary"])
        self.assertEqual([], model.calls)
        self.assertEqual("No information found.", report["rows"][0]["response"])

    def test_answer_metric_still_evaluates_actual_response_when_context_is_empty(self):
        from services import eval_ragas

        row = {"id": "kt_004", "user_input": "Question?", "reference": "Reference."}
        with (
            patch.object(eval_ragas.default_db, "count", return_value=1),
            patch.object(eval_ragas, "_resolve_golden_testset_path", return_value=__file__),
            patch.object(eval_ragas, "_load_golden_testset", return_value=[row]),
            patch.object(eval_ragas, "answer_question", return_value=("No information found.", [])),
            patch.object(eval_ragas, "_build_local_llm"),
            patch.object(eval_ragas, "evaluate", return_value={"factual_correctness": 0.25}) as evaluate,
            self.assertLogs("services.eval_ragas", level="WARNING"),
        ):
            report = eval_ragas.run_ragas_benchmark(
                sample_size=1, full_metrics=True, metric_keys=["factual_correctness"],
            )
        sample = evaluate.call_args.kwargs["dataset"].samples[0]
        self.assertEqual([], sample.retrieved_contexts)
        self.assertEqual("No information found.", sample.response)
        self.assertEqual({"factual_correctness": 0.25}, report["summary"])

    def test_invalid_input_and_retrieval_errors_still_stop_benchmark(self):
        from services import eval_ragas

        for reference_contexts, retrieval_error, message in (
            ([], None, "reference_contexts"),
            (["Context."], RuntimeError("retrieval unavailable"), "retrieval unavailable"),
        ):
            row = {
                "id": "kt_004", "user_input": "Question?",
                "reference_contexts": reference_contexts,
            }
            with (
                self.subTest(message=message),
                patch.object(eval_ragas.default_db, "count", return_value=1),
                patch.object(eval_ragas, "_resolve_golden_testset_path", return_value=__file__),
                patch.object(eval_ragas, "_load_golden_testset", return_value=[row]),
                patch.object(eval_ragas, "_retrieve_contexts_for_benchmark", side_effect=retrieval_error),
                patch.object(eval_ragas, "evaluate") as evaluate,
                self.assertRaisesRegex(RuntimeError, message),
            ):
                eval_ragas.run_ragas_benchmark(sample_size=1)
            evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
