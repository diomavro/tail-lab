// Typed API client mirroring the backend's pydantic response models
// (src/tail_lab/api/schemas.py). Keep these two in sync by hand for now —
// this is the one endpoint the walking skeleton has.

export interface VixStretchResponse {
  date: string
  close: number
  rolling_mean_20d: number
  rolling_std_20d: number
  z_score: number
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
