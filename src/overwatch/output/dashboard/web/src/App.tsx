import { useEffect, useRef, useState } from 'react'
import { fetchState, type DashboardState } from './api'

const DEFAULT_POLL_SECONDS = 5

// Operator console shell (#124). This is the SPA + data-API scaffold that the
// dashboard slices build on: the live camera feed + detection overlays (#119 /
// #120), the rich alerts strip + info panel (#121), and client-side overlays
// (#122) land on top of this shell — it deliberately stays minimal for now.
export default function App() {
  const [state, setState] = useState<DashboardState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()

    const poll = async () => {
      try {
        const next = await fetchState(controller.signal)
        if (cancelled) return
        setState(next)
        setError(null)
        timer.current = window.setTimeout(poll, next.refresh_seconds * 1000)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
        timer.current = window.setTimeout(poll, DEFAULT_POLL_SECONDS * 1000)
      }
    }

    poll()
    return () => {
      cancelled = true
      controller.abort()
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [])

  return (
    <main>
      <header>
        <h1>Overwatch — operator console</h1>
        <p className="meta">
          Console shell (#124). Live camera feed, detection overlays and the full
          alerts UI arrive in #119 / #120 / #121.
        </p>
      </header>

      {error && <p className="banner error">data unavailable — {error}</p>}
      {!state && !error && <p className="banner">loading…</p>}

      {state && (
        <>
          <section>
            <h2>Zone counts</h2>
            {state.zone_counts.length === 0 ? (
              <p className="empty">no zones reporting</p>
            ) : (
              <ul className="zones">
                {state.zone_counts.map((z) => (
                  <li key={z.zone_id}>
                    <span className="zone-id">{z.zone_id}</span>
                    <span className="zone-count">{z.count}</span>
                    {z.class_name && <span className="zone-class">{z.class_name}</span>}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h2>Recent alerts</h2>
            {state.recent_alerts.length === 0 ? (
              <p className="empty">no recent alerts</p>
            ) : (
              <ul className="alerts">
                {state.recent_alerts.map((a, i) => (
                  <li key={i} className={`sev-${a.severity}`}>
                    <span className="sev">{a.severity}</span>
                    <span className="title">{a.title}</span>
                    <span className="message">{a.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <footer className="meta">
            updated {new Date(state.generated_at * 1000).toLocaleTimeString()} ·
            polling every {state.refresh_seconds}s · read-only
          </footer>
        </>
      )}
    </main>
  )
}
