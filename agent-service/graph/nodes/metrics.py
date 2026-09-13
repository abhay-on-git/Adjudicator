"""Per-node token/cost/latency recording.

Every graph node writes a `{node_name: {tokens, cost_usd, latency_ms}}`
entry into `AdjudicationState.node_metrics`. The state's merge-by-key
reducer unions those entries across the run so later nodes never clobber
earlier ones.

LLM nodes (extraction, explanation) also pull accumulated token usage
from `graph.nodes.llm_utils` — `parse_structured` records usage from the
provider response on every call, including retries.
"""

from __future__ import annotations

import functools
import inspect
import time
from typing import Awaitable, Callable

from graph.nodes.llm_utils import consume_llm_usage, reset_llm_usage

NodeFn = Callable[..., dict]
AsyncNodeFn = Callable[..., Awaitable[dict]]


def attach_metrics(node: str, result: dict, started: float, *, llm: bool = False) -> dict:
    latency_ms = (time.perf_counter() - started) * 1000.0
    tokens_in = 0
    tokens_out = 0
    cost_usd = 0.0
    if llm:
        usage = consume_llm_usage()
        tokens_in = usage.tokens_in
        tokens_out = usage.tokens_out
        cost_usd = usage.cost_usd
    result["node_metrics"] = {
        node: {
            "tokens": tokens_in + tokens_out,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(cost_usd, 8),
            "latency_ms": round(latency_ms, 2),
        }
    }
    return result


def timed_node(node_name: str, *, llm: bool = False):
    """Decorator: time the node and attach `node_metrics` to its return dict.

    `llm=True` resets the usage accumulator on entry and consumes it on exit,
    so retries inside the node are summed rather than dropped.
    """

    def decorator(fn: NodeFn | AsyncNodeFn):
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(state):
                started = time.perf_counter()
                if llm:
                    reset_llm_usage()
                result = await fn(state)
                return attach_metrics(node_name, result, started, llm=llm)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(state):
            started = time.perf_counter()
            result = fn(state)
            return attach_metrics(node_name, result, started, llm=False)

        return sync_wrapper

    return decorator
