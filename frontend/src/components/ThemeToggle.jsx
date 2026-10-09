import { useTheme } from '../lib/themeContext'

function Sun() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
      <circle cx="10" cy="10" r="3.5" />
      <path d="M10 2.5v1.8M10 15.7v1.8M2.5 10h1.8M15.7 10h1.8M4.7 4.7l1.3 1.3M14 14l1.3 1.3M15.3 4.7 14 6M6 14l-1.3 1.3" />
    </svg>
  )
}

function Moon() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden="true">
      <path d="M16.5 11.6A6.5 6.5 0 0 1 8.4 3.5a6.5 6.5 0 1 0 8.1 8.1Z" />
    </svg>
  )
}

/** Switches between light and dark. The icon shows the mode you will get. */
export default function ThemeToggle({ className = '' }) {
  const { theme, toggle } = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
      data-testid="theme-toggle"
      className={`rounded-md border border-axis bg-surface-2 p-2 text-ink transition-colors hover:border-accent hover:text-accent ${className}`}
    >
      {theme === 'dark' ? <Sun /> : <Moon />}
    </button>
  )
}
