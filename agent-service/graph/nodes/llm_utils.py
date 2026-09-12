"""Shared LLM helpers: retry-with-backoff and provider-aware structured parse.

Retry loop backs degradation mode 3 in DESIGN.md. `parse_structured` routes
OpenAI through Responses structured outputs and MiniMax through chat
completions + JSON schema prompt + Pydantic validation (MiniMax Responses
does not support json_schema text formats).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Awaitable, Callable, Never, TypeVar

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from graph.llm_config import LlmProvider, get_async_client, load_llm_settings

T = TypeVar("T")
TModel = TypeVar("TModel", bound=BaseModel)

RETRYABLE_ERRORS = (RateLimitError, APIConnectionError, APIStatusError)

_THINK_TAG_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


async def call_with_backoff(
    make_call: Callable[[], Awaitable[T]],
    max_retries: int,
    backoff_seconds: list[float],
) -> tuple[T | None, str | None]:
    """Returns (result, None) on success, or (None, failure_reason) if every
    attempt raised a retryable OpenAI error."""
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await make_call(), None
        except RETRYABLE_ERRORS as exc:
            last_error = exc
            if attempt < max_retries - 1:
                await asyncio.sleep(backoff_seconds[attempt])
    return None, f"{type(last_error).__name__}: {last_error}"


def _strip_thinking(text: str) -> str:
    return _THINK_TAG_RE.sub("", text).strip()


def _extract_json_object(text: str) -> str:
    cleaned = _strip_thinking(text)
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    match = _JSON_OBJECT_RE.search(cleaned)
    if match is None:
        raise _validation_missing("JsonObject", cleaned)
    return match.group(0)


def _validation_missing(model_name: str, detail: object) -> ValidationError:
    return ValidationError.from_exception_data(
        model_name,
        [{"type": "missing", "loc": (), "input": detail}],
    )


async def _parse_openai(
    client: AsyncOpenAI,
    *,
    model: str,
    system: str,
    user: str,
    text_format: type[TModel],
) -> TModel:
    response = await client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        text_format=text_format,
    )
    parsed = response.output_parsed
    if parsed is None:
        refusal = getattr(response, "output_text", None) or "model returned no parsed output"
        raise _validation_missing(text_format.__name__, refusal)
    return parsed


async def _parse_minimax(
    client: AsyncOpenAI,
    *,
    model: str,
    system: str,
    user: str,
    text_format: type[TModel],
) -> TModel:
    schema = text_format.model_json_schema()
    schema_instruction = (
        "\n\nRespond with a single JSON object only — no markdown fences, no "
        "commentary. The JSON must validate against this schema:\n"
        f"{json.dumps(schema)}"
    )
    completion = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system + schema_instruction},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    content = completion.choices[0].message.content if completion.choices else None
    if not content:
        raise _validation_missing(text_format.__name__, "empty MiniMax completion content")
    try:
        return text_format.model_validate_json(_extract_json_object(content))
    except (ValidationError, json.JSONDecodeError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise _validation_missing(text_format.__name__, str(exc)) from exc


async def parse_structured(
    *,
    system: str,
    user: str,
    text_format: type[TModel],
    client: AsyncOpenAI | None = None,
) -> TModel:
    """Provider-aware structured parse. Optional `client` is a test seam."""
    settings = load_llm_settings()
    model = settings.model
    llm = client if client is not None else get_async_client(settings)

    if settings.provider is LlmProvider.OPENAI:
        return await _parse_openai(
            llm, model=model, system=system, user=user, text_format=text_format
        )
    if settings.provider is LlmProvider.MINIMAX:
        return await _parse_minimax(
            llm, model=model, system=system, user=user, text_format=text_format
        )
    unreachable: Never = settings.provider
    raise RuntimeError(f"Unhandled LLM provider: {unreachable}")
