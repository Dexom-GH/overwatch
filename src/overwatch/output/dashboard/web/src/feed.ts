// Merge the EventStore alerts + events into one newest-first activity feed for
// the console's alerts strip (#121). Pure functions — no React, no I/O.

import type { AlertRow, DashboardState, EventRow } from './api'

// `itemType` is the discriminant; note EventRow already has its own `kind`
// (the event type, e.g. "fence_crossing"), which we must not clobber.
export interface AlertItem extends AlertRow {
  itemType: 'alert'
  key: string
}

export interface EventItem extends EventRow {
  itemType: 'event'
  key: string
}

export type ActivityItem = AlertItem | EventItem

// Stable-ish key for "is this item new since the last poll?" and React keys.
function alertKey(a: AlertRow): string {
  return `a:${a.timestamp}:${a.severity}:${a.title}`
}

function eventKey(e: EventRow): string {
  return `e:${e.timestamp}:${e.kind}:${e.track_id ?? ''}:${e.zone_id ?? ''}`
}

/** Alerts + events as one feed, newest first. */
export function mergeActivity(state: DashboardState): ActivityItem[] {
  const items: ActivityItem[] = [
    ...state.recent_alerts.map((a): AlertItem => ({ ...a, itemType: 'alert', key: alertKey(a) })),
    ...state.recent_events.map((e): EventItem => ({ ...e, itemType: 'event', key: eventKey(e) })),
  ]
  items.sort((x, y) => y.timestamp - x.timestamp)
  return items
}

/** A short, operator-readable label for an event kind ("fence_crossing" -> "Fence crossing"). */
export function humanizeEventKind(kind: string): string {
  const s = kind.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** Relative "12s ago" / "3m ago" string, given a reference now (epoch seconds). */
export function relativeTime(ts: number, nowSeconds: number): string {
  const delta = Math.max(0, Math.round(nowSeconds - ts))
  if (delta < 60) return `${delta}s ago`
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`
  return `${Math.round(delta / 3600)}h ago`
}
