import { ErrorBox, Skeleton } from './ui'

/**
 * Wraps a useApi() result. It shows a skeleton while loading, an error with Retry on failure,
 * and the content otherwise, so no screen is ever blank. If a reload fails but earlier data
 * exists, the data stays and a small banner explains.
 */
export default function Async({ state, children, label = 'Loading', rows = 3 }) {
  const { data, error, loading, reload } = state
  if (error && data == null) return <ErrorBox message={error} onRetry={reload} />
  if (data == null) return <Skeleton rows={rows} label={label} />
  return (
    <>
      {error && (
        <p role="alert" className="mb-2 text-xs text-flag">
          Could not refresh: {error}{' '}
          <button type="button" className="underline" onClick={reload}>
            Try again
          </button>
        </p>
      )}
      <div aria-busy={loading} className={loading ? 'opacity-70 transition-opacity' : undefined}>
        {children(data)}
      </div>
    </>
  )
}
