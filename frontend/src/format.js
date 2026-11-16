import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

export const fmtDate = (v) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '—')
export const fmtAgo = (v) => (v ? dayjs(v).fromNow() : '—')
export const fmtPct = (v) => (v === null || v === undefined ? '—' : `${Math.round(v)}%`)
export const fmtGB = (v) => (v === null || v === undefined ? '—' : v >= 1024 ? `${(v / 1024).toFixed(1)} TB` : `${Math.round(v)} GB`)
export const fmtMem = (mb) => (mb ? (mb >= 1024 ? `${+(mb / 1024).toFixed(1)} GB` : `${mb} MB`) : '—')

export function fmtUptime(seconds) {
  if (!seconds) return '—'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`
}

export const POWER = {
  poweredOn: { label: 'On', color: 'success' },
  poweredOff: { label: 'Off', color: 'default' },
  suspended: { label: 'Suspended', color: 'warning' },
  deleted: { label: 'Deleted', color: 'error' },
}

export function fmtRate(bps) {
  if (bps === null || bps === undefined) return '—'
  const units = ['B/s', 'KB/s', 'MB/s', 'GB/s']
  let v = bps
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

export const AGENT_STATE = {
  online: { label: 'Online', color: 'success' },
  offline: { label: 'Offline', color: 'warning' },
  revoked: { label: 'Revoked', color: 'default' },
  none: { label: 'No agent', color: 'default' },
}

export const TOOLS_STATUS = {
  guestToolsRunning: { label: 'Running', color: 'success' },
  guestToolsNotRunning: { label: 'Not running', color: 'warning' },
  guestToolsExecutingScripts: { label: 'Starting', color: 'processing' },
}

export const OS_LABEL ={ windows: 'Windows', linux: 'Linux', other: 'Other' }

export const EVENT_KINDS = {
  discovered: { label: 'Discovered', color: 'default' },
  created: { label: 'Created', color: 'blue' },
  deleted: { label: 'Deleted', color: 'red' },
  reappeared: { label: 'Reappeared', color: 'cyan' },
  powered_on: { label: 'Powered on', color: 'green' },
  powered_off: { label: 'Powered off', color: 'default' },
  suspended: { label: 'Suspended', color: 'gold' },
  renamed: { label: 'Renamed', color: 'purple' },
  ip_changed: { label: 'IP changed', color: 'orange' },
  reconfigured: { label: 'Resized', color: 'geekblue' },
  owner_changed: { label: 'Owner changed', color: 'magenta' },
  problem: { label: 'Problem', color: 'red' },
  resolved: { label: 'Resolved', color: 'green' },
  critical_changed: { label: 'Critical flag', color: 'volcano' },
}

export const TASK_STATUS = {
  pending: { label: 'Waiting for agent', color: 'default' },
  sent: { label: 'Sent', color: 'processing' },
  running: { label: 'Running', color: 'processing' },
  succeeded: { label: 'Succeeded', color: 'success' },
  failed: { label: 'Failed', color: 'error' },
  timeout: { label: 'Timed out', color: 'warning' },
  cancelled: { label: 'Cancelled', color: 'default' },
  skipped: { label: 'Skipped', color: 'default' },
  expired: { label: 'Expired', color: 'warning' },
  lost: { label: 'No result', color: 'error' },
}

export const TASK_STATE = {
  running: { label: 'Running', color: 'processing' },
  succeeded: { label: 'Succeeded', color: 'success' },
  partial: { label: 'Partly failed', color: 'warning' },
  failed: { label: 'Failed', color: 'error' },
  cancelled: { label: 'Cancelled', color: 'default' },
  skipped: { label: 'Nothing ran', color: 'default' },
}

export function fmtDuration(start, end) {
  if (!start) return '—'
  const s = Math.max(0, Math.round(((end ? Date.parse(end) : Date.now()) - Date.parse(start)) / 1000))
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

export const HEALTH = {
  critical: { label: 'Critical', color: 'error' },
  warning: { label: 'Warning', color: 'warning' },
  ok: { label: 'OK', color: 'success' },
}

export const INTERVAL_LABEL = (s) => (s >= 3600 ? `${s / 3600} h` : s >= 60 ? `${s / 60} min` : `${s} s`)

// Status palette (fixed): used for utilisation bars, always shown with the number beside it.
export const STATUS = { good: '#0ca30c', warning: '#fab219', serious: '#ec835a', critical: '#d03b3b' }

export function usageColor(pct) {
  if (pct === null || pct === undefined) return undefined
  if (pct >= 90) return STATUS.critical
  if (pct >= 80) return STATUS.serious
  if (pct >= 70) return STATUS.warning
  return STATUS.good
}
