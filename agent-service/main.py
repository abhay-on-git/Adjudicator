"""FastAPI app for the agent-service.

Owns the compiled LangGraph graph's lifecycle: compiled once at startup
(with its file-backed checkpointer connection opened), closed once at
shutdown. Real routes live in routes.py (see that module's docstring); this
file also keeps the streaming spike endpoint as historical evidence for the
"streaming transport" fork in DESIGN.md — it predates the real graph and is
no longer load-bearing, but is cheap to leave in place.
"""

import asyncio
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from graph.build_graph import compile_graph
from routes import router as claims_router

load_dotenv()

CHECKPOINT_DB_PATH = "checkpoints/adjudicator.sqlite"


@asynccontextmanager
async def lifespan(app: FastAPI):
    graph, conn = await compile_graph(CHECKPOINT_DB_PATH)
    app.state.graph = graph
    app.state.checkpoint_conn = conn
    try:
        yield
    finally:
        await conn.close()


app = FastAPI(title="venxr-adjudicator-agent-service", lifespan=lifespan)
app.include_router(claims_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


async def _spike_chunks():
    """Yields 5 chunks, ~1 second apart, each stamped with a server-side send time.

    This is the isolated streaming spike requested before building the real
    submit -> stream -> render pipeline: proves (or disproves) that chunks are
    delivered incrementally end-to-end through the Django proxy, rather than
    buffered into one response at the end.
    """
    for i in range(5):
        payload = f"data: chunk {i} sent_at={time.time():.3f}\n\n"
        yield payload.encode("utf-8")
        await asyncio.sleep(1)


@app.get("/spike/stream")
async def spike_stream():
    return StreamingResponse(_spike_chunks(), media_type="text/event-stream")
