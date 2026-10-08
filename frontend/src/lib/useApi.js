import { useCallback, useEffect, useState } from 'react'
import { errorMessage } from '../api'

/**
 * Load data with loading, error and reload handling. `fetcher(signal)` returns a promise.
 * Earlier data stays visible while a reload is in flight or after a failed reload.
 */
export function useApi(fetcher, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  const [tick, setTick] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setState((s) => ({ ...s, loading: true, error: null }))
    fetcher(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ data, error: null, loading: false })
      },
      (err) => {
        if (controller.signal.aborted || err?.code === 'ERR_CANCELED') return
        setState((s) => ({ data: s.data, error: errorMessage(err), loading: false }))
      },
    )
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { ...state, reload }
}

export function useDebounced(value, delay = 300) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(id)
  }, [value, delay])
  return debounced
}
