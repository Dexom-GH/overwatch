import { useEffect, useRef, useState } from 'react'
import { fetchState, type DashboardState } from './api'
import { humanizeEventKind, mergeActivity, relativeTime, type ActivityItem } from './feed'

const DEFAULT_POLL_SECONDS = 5

// Operator-console shell: info panel + live activity strip (#121), built on the
// SPA + JSON-API scaffold (#124). It short-polls /api/state, so new alerts and
// events appear without a manual refresh. The live camera feed + detection
// overlays land on top of this shell in #119 / #120 / #122.
export default function App() {
  const [state, setState] = useState<DashboardState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const seen = useRef<Set<string> | null>(null) // null until first load (no first-load highlight)

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

  const activity = state ? mergeActivity(state) : []
  // Mark items not seen on the previous poll as "new" (skip the very first load).
  const newKeys = new Set<string>()
  if (state) {
    if (seen.current === null) {
      seen.current = new Set(activity.map((i) => i.key))
    } else {
      for (const item of activity) {
        if (!seen.current.has(item.key)) {
          newKeys.add(item.key)
          seen.current.add(item.key)
        }
      }
    }
  }

  return (
    <main>
      <header>
        <h1>Overwatch — operator console</h1>
        <p className="meta">
          Live monitoring — camera feed with detection overlays, counts and alerts.
        </p>
      </header>

      <LiveFeed />

      {error && <p className="banner error">data unavailable — {error}</p>}
      {!state && !error && <p className="banner">connecting…</p>}

      {state && (
        <>
          <InfoPanel state={state} />

          <section>
            <h2>Activity</h2>
            <ActivityStrip items={activity} newKeys={newKeys} now={state.generated_at} />
          </section>

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

          <footer className="meta">
            updated {new Date(state.generated_at * 1000).toLocaleTimeString()} ·
            polling every {state.refresh_seconds}s · read-only
          </footer>
        </>
      )}
    </main>
  )
}

// The live MJPEG feed (#120): a burned-in detection feed served at /api/feed
// (multipart/x-mixed-replace) renders natively in an <img>. When the pipeline
// isn't running the endpoint is absent (404) — show an "offline" placeholder and
// retry periodically so the feed reappears once the pipeline comes up.
function LiveFeed() {
  const [attempt, setAttempt] = useState(0)
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    if (!offline) return
    const t = window.setTimeout(() => {
      setOffline(false)
      setAttempt((a) => a + 1)
    }, 10000)
    return () => window.clearTimeout(t)
  }, [offline])

  return (
    <section className="feed">
      {offline ? (
        <div className="feed-offline">
          <span className="feed-offline-title">Live feed offline</span>
          <span className="feed-offline-sub">pipeline not running · retrying…</span>
        </div>
      ) : (
        <img
          key={attempt}
          className="feed-img"
          src={`/api/feed?a=${attempt}`}
          alt="live camera feed with detection overlays"
          onError={() => setOffline(true)}
        />
      )}
    </section>
  )
}

function InfoPanel({ state }: { state: DashboardState }) {
  const s = state.summary
  const cards: Array<{ label: string; value: string; tone?: string }> = [
    { label: 'Animals in view', value: String(s.total_count) },
    { label: 'Zones reporting', value: String(s.zones_reporting) },
    {
      label: 'Critical alerts',
      value: String(s.critical_alert_count),
      tone: s.critical_alert_count > 0 ? 'critical' : undefined,
    },
    { label: 'Recent alerts', value: String(s.recent_alert_count) },
    {
      label: 'Last activity',
      value: s.last_activity_at === null ? '—' : relativeTime(s.last_activity_at, state.generated_at),
    },
  ]
  return (
    <section className="info-panel">
      {cards.map((c) => (
        <div key={c.label} className={`stat${c.tone ? ` stat-${c.tone}` : ''}`}>
          <div className="stat-value">{c.value}</div>
          <div className="stat-label">{c.label}</div>
        </div>
      ))}
    </section>
  )
}

function ActivityStrip({
  items,
  newKeys,
  now,
}: {
  items: ActivityItem[]
  newKeys: Set<string>
  now: number
}) {
  if (items.length === 0) {
    return <p className="empty">no recent activity</p>
  }
  return (
    <ul className="activity">
      {items.map((item) => {
        const isNew = newKeys.has(item.key)
        if (item.itemType === 'alert') {
          return (
            <li key={item.key} className={`row sev-${item.severity}${isNew ? ' is-new' : ''}`}>
              <span className="badge sev">{item.severity}</span>
              <span className="row-title">{item.title}</span>
              <span className="row-detail">{item.message}</span>
              <span className="row-time">{relativeTime(item.timestamp, now)}</span>
            </li>
          )
        }
        return (
          <li key={item.key} className={`row kind-event${isNew ? ' is-new' : ''}`}>
            <span className="badge event">event</span>
            <span className="row-title">{humanizeEventKind(item.kind)}</span>
            <span className="row-detail">
              {item.zone_id ? `zone ${item.zone_id}` : ''}
              {item.zone_id && item.track_id != null ? ' · ' : ''}
              {item.track_id != null ? `track ${item.track_id}` : ''}
            </span>
            <span className="row-time">{relativeTime(item.timestamp, now)}</span>
          </li>
        )
      })}
    </ul>
  )
}
