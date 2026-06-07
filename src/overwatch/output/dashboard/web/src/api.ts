// Typed client for the operator-console JSON data API (#124, ADR-0008).
// Mirrors `state_dict` in output/dashboard/server.py — keep the two in sync.

export interface ZoneCount {
  zone_id: string
  timestamp: number
  count: number
  class_name: string | null
}

export interface AlertRow {
  timestamp: number
  severity: string
  title: string
  message: string
}

export interface EventRow {
  timestamp: number
  kind: string
  track_id: number | null
  zone_id: string | null
}

export interface DashboardState {
  generated_at: number
  refresh_seconds: number
  zone_counts: ZoneCount[]
  recent_alerts: AlertRow[]
  recent_events: EventRow[]
}

export async function fetchState(signal?: AbortSignal): Promise<DashboardState> {
  const resp = await fetch('/api/state', { signal })
  if (!resp.ok) {
    throw new Error(`/api/state returned ${resp.status}`)
  }
  return (await resp.json()) as DashboardState
}
