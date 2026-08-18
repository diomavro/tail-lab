// Typed API client mirroring the backend's pydantic response models
// (src/tail_lab/api/schemas.py). Keep these in sync by hand.

export interface VixStretchResponse {
  date: string
  close: number
  rolling_mean_20d: number
  rolling_std_20d: number
  z_score: number
}

export type FeedbackKind = 'big_picture' | 'issue'

export interface FeedbackRecord {
  id: string
  text: string
  kind: FeedbackKind
  created_at: string
  status: 'open' | 'resolved'
  resolved_at: string | null
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function fetchVixStretch(signal?: AbortSignal): Promise<VixStretchResponse> {
  const resp = await fetch('/api/vix/stretch', { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/vix/stretch failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as VixStretchResponse
}

// Public, unauthenticated write (see api/feedback_routes.py) -- the
// token-gated GET/resolve routes are for the daily agent only and are
// deliberately not called from the browser.
export async function submitFeedback(
  text: string,
  kind: FeedbackKind,
  signal?: AbortSignal,
): Promise<FeedbackRecord> {
  const resp = await fetch('/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, kind }),
    signal,
  })
  if (!resp.ok) {
    throw new ApiError(`POST /api/feedback failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as FeedbackRecord
}
