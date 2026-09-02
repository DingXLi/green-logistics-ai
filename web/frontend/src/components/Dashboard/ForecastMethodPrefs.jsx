/**
 * ForecastMethodPrefs - shows persisted best_method per metric (iter #36)
 *
 * 数据源: GET /api/persistence/forecast-method-prefs
 *
 * Background (iter #35): forecast confidence endpoint auto-persists the
 * best-performing method per metric (cost_sek / co2_kg / util_pct / matches)
 * based on R² across history_n samples. This panel surfaces that stored
 * preference so an operator can verify which method the auto-resolver
 * would pick for each metric.
 *
 * Display:
 * - One row per metric showing:
 *   - metric icon + name
 *   - best_method (with human-readable label)
 *   - R² (color-coded: green ≥0.9, amber ≥0.7, red <0.7)
 *   - n_samples (confidence counter — higher = more reliable)
 *   - updated_at (relative time, e.g. "2h ago")
 * - Empty state: "No prefs yet — defaults to linear"
 * - Auth-required state (HTTP 401): friendly message asking operator to
 *   set GL_ADMIN_TOKEN on server (or call /api/admin/auth/status to check)
 *
 * Auto-refresh every 60s (matches other panels' cadence).
 */

import { useState, useEffect } from 'react'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_INTERVAL_MS = 60000

const METRIC_META = {
  cost_sek:  { icon: '💰', label: 'Cost (SEK)' },
  co2_kg:    { icon: '🌱', label: 'CO₂ (kg)' },
  util_pct:  { icon: '🚛', label: 'Utilization' },
  matches:   { icon: '🤝', label: 'Matches' },
}

const METHOD_LABEL = {
  linear: 'Linear',
  moving_average: 'Moving Avg',
  exponential_smoothing: 'Exp. Smoothing',
}

function r2Color(r2) {
  if (r2 == null) return '#94a3b8'
  if (r2 >= 0.9) return '#22c55e'   // green — strong fit
  if (r2 >= 0.7) return '#f59e0b'   // amber — moderate
  return '#ef4444'                  // red — weak
}

function relativeTime(iso) {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const diff = Date.now() - then
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.floor(hr / 24)
  return `${day}d ago`
}

export function ForecastMethodPrefs() {
  const [prefs, setPrefs] = useState([])
  const [count, setCount] = useState(0)
  const [metricsCovered, setMetricsCovered] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [authRequired, setAuthRequired] = useState(false)

  useEffect(() => {
    let cancelled = false
    const fetchData = async () => {
      try {
        const resp = await fetch(`${API_BASE}/persistence/forecast-method-prefs`)
        if (resp.status === 401) {
          if (!cancelled) {
            setAuthRequired(true)
            setError(null)
            setLoading(false)
          }
          return
        }
        if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`)
        const data = await resp.json()
        if (!cancelled) {
          setPrefs(data.prefs || [])
          setCount(data.count || 0)
          setMetricsCovered(data.metrics_covered || [])
          setAuthRequired(false)
          setError(null)
          setLoading(false)
        }
      } catch (e) {
        if (!cancelled) {
          setError(e.message || 'fetch failed')
          setLoading(false)
        }
      }
    }
    fetchData()
    const interval = setInterval(fetchData, REFRESH_INTERVAL_MS)
    return () => { cancelled = true; clearInterval(interval) }
  }, [])

  if (loading) return <LoadingSpinner label="Loading forecast method prefs…" />
  if (authRequired) {
    return (
      <div className="chart-card">
        <div className="chart-card-header">
          <h3>🎯 Forecast Method Prefs</h3>
          <span className="chart-card-sub">Best method per metric (auto-persisted)</span>
        </div>
        <div className="info-banner" style={{ marginTop: '0.75rem', padding: '0.75rem', borderRadius: '4px', background: '#1e3a5f', border: '1px solid #3b82f6', color: '#bfdbfe' }}>
          🔒 Admin token required to view persisted preferences.
          <div style={{ marginTop: '0.25rem', fontSize: '0.85rem' }}>
            Check <code>GET /api/admin/auth/status</code> to see if
            <code> GL_ADMIN_TOKEN</code> is configured on the server.
          </div>
        </div>
      </div>
    )
  }
  if (error) {
    return (
      <div className="chart-card">
        <div className="chart-card-header">
          <h3>🎯 Forecast Method Prefs</h3>
        </div>
        <div className="error-banner" style={{ marginTop: '0.75rem' }}>⚠️ {error}</div>
      </div>
    )
  }

  // Build the row list: union of all 4 metrics + any extras from prefs.
  // This guarantees we always show all 4 known metrics even when prefs is empty.
  const knownMetrics = ['cost_sek', 'co2_kg', 'util_pct', 'matches']
  const allMetrics = Array.from(new Set([...knownMetrics, ...metricsCovered]))
  const prefMap = new Map(prefs.map(p => [p.metric, p]))

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <h3>🎯 Forecast Method Prefs</h3>
        <span className="chart-card-sub">
          {count > 0
            ? `${count} metric${count === 1 ? '' : 's'} tracked · used by /forecast?method=auto`
            : 'Best method per metric (auto-persisted)'}
        </span>
      </div>
      {count === 0 ? (
        <div className="empty-state" style={{ marginTop: '0.75rem', padding: '0.75rem', color: '#94a3b8' }}>
          No prefs persisted yet — <code>/forecast?method=auto</code> will use
          <strong> linear</strong> as the default. Preferences are learned
          automatically by the forecast-confidence endpoint as cycle history grows.
        </div>
      ) : (
        <div className="forecast-prefs-grid" style={{ marginTop: '0.75rem', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.5rem' }}>
          {allMetrics.map(metricKey => {
            const meta = METRIC_META[metricKey] || { icon: '📊', label: metricKey }
            const pref = prefMap.get(metricKey)
            return (
              <div key={metricKey}
                className="forecast-pref-row"
                style={{
                  padding: '0.6rem 0.75rem',
                  borderRadius: '4px',
                  background: '#1e293b',
                  border: '1px solid #475569',
                  borderLeft: pref ? `4px solid ${r2Color(pref.r_squared)}` : '4px solid #475569',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ color: '#e2e8f0', fontSize: '0.9rem' }}>
                    {meta.icon} {meta.label}
                  </span>
                  <span style={{ color: '#94a3b8', fontSize: '0.75rem' }}>
                    {pref ? `${pref.n_samples}× sample${pref.n_samples === 1 ? '' : 's'}` : 'no data'}
                  </span>
                </div>
                <div style={{ marginTop: '0.35rem', display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ color: pref ? '#f1f5f9' : '#64748b', fontSize: '0.95rem', fontWeight: 500 }}>
                    {pref ? METHOD_LABEL[pref.best_method] || pref.best_method : '—'}
                  </span>
                  <span style={{
                    color: r2Color(pref?.r_squared),
                    fontSize: '0.85rem',
                    fontFamily: 'monospace',
                  }}>
                    {pref ? `R²=${pref.r_squared.toFixed(3)}` : ''}
                  </span>
                </div>
                {pref && (
                  <div style={{ marginTop: '0.25rem', color: '#64748b', fontSize: '0.7rem' }}>
                    updated {relativeTime(pref.updated_at)} · history_n={pref.history_n}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default ForecastMethodPrefs
