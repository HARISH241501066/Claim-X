import { Component } from 'react'

/** Last line of defence: a rendering crash shows a message instead of a blank screen. */
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Unhandled UI error', error, info?.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div role="alert" className="m-6 rounded-xl border border-critical/60 bg-critical/10 p-6">
        <h1 className="text-lg font-semibold text-ink">This screen could not be shown</h1>
        <p className="mt-2 text-sm text-ink-2">{String(this.state.error.message ?? this.state.error)}</p>
        <button
          type="button"
          className="mt-4 rounded-md border border-axis px-3 py-1.5 text-sm text-ink hover:border-accent"
          onClick={() => this.setState({ error: null })}
        >
          Try again
        </button>
      </div>
    )
  }
}
