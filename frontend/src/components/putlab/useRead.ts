import { useEffect, useState } from 'react'
import { ApiError } from '../../api/client'

/** A read that can be absent for a reason the page should word politely:
 *  `missing` is a 404 or 422 (the server has no answer for this name), `error`
 *  is anything else. */
export type Read<T> =
  | { status: 'loading' }
  | { status: 'ready'; data: T }
  | { status: 'missing'; message: string }
  | { status: 'error'; message: string }

/** Fetch once per `key` (null = do not fetch yet), aborting a superseded read. */
export function useRead<T>(key: string | null, fetcher: (signal: AbortSignal) => Promise<T>): Read<T> {
  const [read, setRead] = useState<Read<T>>({ status: 'loading' })
  useEffect(() => {
    if (key === null) return
    const ctl = new AbortController()
    setRead({ status: 'loading' })
    fetcher(ctl.signal)
      .then((data) => setRead({ status: 'ready', data }))
      .catch((err: unknown) => {
        if (ctl.signal.aborted) return
        const message = err instanceof Error ? err.message : 'request failed'
        const absent = err instanceof ApiError && (err.status === 404 || err.status === 422)
        setRead({ status: absent ? 'missing' : 'error', message })
      })
    return () => ctl.abort()
    // The key IS the identity of the read; the fetcher closes over it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return read
}
