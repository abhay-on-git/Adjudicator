import type { ClaimDetail, ClaimSubmission, StreamEvent } from './types'

/**
 * Parses a fetch() response body as Server-Sent Events, yielding one
 * `StreamEvent` per complete `event:`/`data:` block AS SOON AS it arrives —
 * this is the frontend half of the same incremental-delivery property the
 * streaming spike proved for Django -> agent-service (see DESIGN.md).
 * `response.body` is a ReadableStream<Uint8Array>; chunk boundaries from the
 * network don't necessarily line up with SSE event boundaries, so we buffer
 * decoded text and only yield once we've seen a full blank-line-terminated
 * block, mirroring the same parsing approach used server-side
 * (backend/claims/views.py's `_parse_sse_block`).
 */
export async function* streamSSE(response: Response): AsyncGenerator<StreamEvent> {
  if (!response.body) {
    throw new Error('Response has no readable body (streaming not supported here).')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const rawBlock = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const parsed = parseSSEBlock(rawBlock)
        if (parsed) yield parsed
        boundary = buffer.indexOf('\n\n')
      }
    }
  } finally {
    reader.releaseLock()
  }
}

function parseSSEBlock(rawBlock: string): StreamEvent | null {
  let eventName: string | null = null
  let dataLine: string | null = null
  for (const line of rawBlock.split('\n')) {
    if (line.startsWith('event: ')) eventName = line.slice('event: '.length)
    else if (line.startsWith('data: ')) dataLine = line.slice('data: '.length)
  }
  if (!eventName || dataLine === null) return null
  return { event: eventName, data: JSON.parse(dataLine) } as StreamEvent
}

async function assertOk(response: Response): Promise<Response> {
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`${response.status} ${response.statusText}: ${body}`)
  }
  return response
}

/** POST /api/claims/ — submit a new claim, returns the raw streaming Response
 * for the caller to feed into `streamSSE`. */
export async function submitClaim(submission: ClaimSubmission): Promise<Response> {
  const response = await fetch('/api/claims/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(submission),
  })
  return assertOk(response)
}

/** POST /api/claims/{id}/resume/ — resume a claim paused in escalation. */
export async function resumeClaim(claimId: string, humanResponse: unknown): Promise<Response> {
  const response = await fetch(`/api/claims/${encodeURIComponent(claimId)}/resume/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ human_response: humanResponse }),
  })
  return assertOk(response)
}

/** GET /api/claims/{id}/ — non-streaming snapshot, served from Django's DB. */
export async function getClaim(claimId: string): Promise<ClaimDetail> {
  const response = await fetch(`/api/claims/${encodeURIComponent(claimId)}/`)
  await assertOk(response)
  return response.json()
}
