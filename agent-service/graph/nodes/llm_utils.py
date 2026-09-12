"""Shared retry-with-backoff helper for the two LLM-backed nodes
(extraction.py, explanation.py) — degradation mode 3 in DESIGN.md ("LLM call
errors or is rate limited... retry with backoff, 2-3 attempts"). Each node
keeps its own retry-count/backoff-schedule constants (they may reasonably
want different values) and just delegates the loop mechanics here, rather
than each maintaining its own copy of the same try/except/sleep loop.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

from openai import APIConnectionError, APIStatusError, RateLimitError

T = TypeVar("T")

RETRYABLE_ERRORS = (RateLimitError, APIConnectionError, APIStatusError)


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
