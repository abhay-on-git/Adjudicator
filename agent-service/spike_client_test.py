"""One-off spike test: confirms chunks from agent-service's /spike/stream arrive
incrementally through Django's StreamingHttpResponse proxy, not buffered as one
blob at the end.

Run with the agent-service venv's httpx (any client works, this just needs to
hit an HTTP URL): .\\agent-service\\.venv\\Scripts\\python.exe spike_client_test.py
"""

import time

import httpx

URL = "http://127.0.0.1:8000/api/spike/stream/"

print(f"Requesting {URL} ...")
start = time.time()
last_arrival = start

with httpx.stream("GET", URL, timeout=30) as response:
    for line in response.iter_lines():
        if not line.strip():
            continue
        now = time.time()
        gap = now - last_arrival
        last_arrival = now
        print(f"[t={now - start:6.3f}s | gap_since_prev={gap:5.3f}s] {line}")

total = time.time() - start
print(f"\nTotal wall time: {total:.3f}s")
