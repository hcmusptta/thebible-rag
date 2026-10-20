import json
import logging
from typing import Any, Dict, Optional

from jsonschema import ValidationError, validate
from langchain_core.messages import SystemMessage
from langchain_core.outputs import LLMResult
from langchain_core.prompt_values import ChatPromptValue, StringPromptValue
from ragas.llms.base import LangchainLLMWrapper


_OUTPUT_SCHEMA_MARKER = "following schema as specified in JSON Schema:\n"
logger = logging.getLogger(__name__)
_JSON_INSTRUCTION = (
    "Return exactly one JSON object matching the output schema in the prompt. "
    "Do not output markdown, analysis, or text outside the JSON object. "
    "Keep each reason to one short sentence based only on the supplied context. "
    "Do not repeat items. Stop as soon as the required JSON object is complete."
)


class RagasJudgeOutputError(RuntimeError):
    """The judge returned output that cannot be used to compute a metric."""


def extract_ragas_output_schema(
    prompt: StringPromptValue,
) -> Optional[Dict[str, Any]]:
    """Extract the Pydantic JSON schema embedded in a Ragas prompt."""
    prompt_text = prompt.to_string()
    marker_index = prompt_text.find(_OUTPUT_SCHEMA_MARKER)
    if marker_index < 0:
        return None

    schema_text = prompt_text[marker_index + len(_OUTPUT_SCHEMA_MARKER) :].lstrip()
    try:
        schema, _ = json.JSONDecoder().raw_decode(schema_text)
    except (json.JSONDecodeError, TypeError):
        return None

    return schema if isinstance(schema, dict) else None


class SchemaConstrainedOllamaLLMWrapper(LangchainLLMWrapper):
    """Apply each Ragas prompt's exact output schema to the Ollama request."""

    def _model_for_prompt(self, prompt: StringPromptValue):
        schema = extract_ragas_output_schema(prompt)
        if schema is None:
            raise RagasJudgeOutputError(
                "RAGAS prompt has no readable output schema; cannot constrain the judge."
            )
        return self.langchain_llm.model_copy(
            update={"format": schema, "reasoning": False}
        )

    @staticmethod
    def _json_prompt(prompt: StringPromptValue) -> ChatPromptValue:
        return ChatPromptValue(messages=[
            SystemMessage(content=_JSON_INSTRUCTION),
            *prompt.to_messages(),
        ])

    @staticmethod
    def _validate_generations(
        result: LLMResult, schema: Dict[str, Any], n: int,
    ) -> LLMResult:
        generations = [batch[0] for batch in result.generations if batch]
        if len(generations) != n:
            raise RagasJudgeOutputError(
                f"RAGAS judge returned {len(generations)} outputs; expected {n}."
            )
        for generation in generations:
            info = generation.generation_info or {}
            if info.get("done_reason") == "length":
                message = (
                    f"RAGAS judge output for {schema.get('title', 'output')} "
                    f"hit the output token limit ({info.get('eval_count', '?')} tokens). "
                    "No score was computed."
                )
                logger.warning(message)
                raise RagasJudgeOutputError(message)
            try:
                validate(instance=json.loads(generation.text), schema=schema)
            except (json.JSONDecodeError, ValidationError) as error:
                # Reject here so Ragas retries this request, not its JSON repair prompt.
                detail = (
                    f"invalid JSON at character {error.pos}"
                    if isinstance(error, json.JSONDecodeError)
                    else f"schema rule {error.validator!r} at {list(error.absolute_path)!r}"
                )
                message = (
                    f"RAGAS judge output for {schema.get('title', 'output')} "
                    f"failed validation ({detail}, "
                    f"{len(generation.text)} characters). No score was computed."
                )
                logger.warning(message)
                raise RagasJudgeOutputError(message) from error
        # Ragas expects all n completions in the first batch.
        result.generations = [generations]
        return result

    def generate_text(
        self,
        prompt: StringPromptValue,
        n: int = 1,
        temperature: Optional[float] = 0.01,
        stop=None,
        callbacks=None,
    ) -> LLMResult:
        model = self._model_for_prompt(prompt)
        result = model.generate_prompt(
            prompts=[self._json_prompt(prompt)] * n,
            stop=stop, callbacks=callbacks, stream=False,
        )
        return self._validate_generations(result, model.format, n)

    async def agenerate_text(
        self,
        prompt: StringPromptValue,
        n: int = 1,
        temperature: Optional[float] = 0.01,
        stop=None,
        callbacks=None,
    ) -> LLMResult:
        model = self._model_for_prompt(prompt)
        result = await model.agenerate_prompt(
            prompts=[self._json_prompt(prompt)] * n,
            stop=stop, callbacks=callbacks, stream=False,
        )
        return self._validate_generations(result, model.format, n)
