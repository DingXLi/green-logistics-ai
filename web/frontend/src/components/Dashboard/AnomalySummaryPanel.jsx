/**
 * AnomalySummaryPanel.jsx — iter #66
 *
 * Aggregate view of anomalous cycles. Complements AnomalousCycles.jsx by
 * showing the *shape* of anomalies rather than the list:
 * - Anomaly rate (% of total cycles)
 * - Most-anomalous metrics (cost / co2 / util / distance / tons)
 * - Severity distribution (high / medium / low)
 * - Multi-anomaly cycle count (cycles with >= 2 metric anomalies)
 *
 * Data source: GET /api/persistence/anomaly-summary?z_threshold=2.0&min_history=5
 *
 * Renders:
 * - KPI cards: anomaly_rate / total_events / multi_anomaly / top_metric
 * - Horizontal bar chart of per-metric anomaly counts
 * - Severity distribution badges
 */
import { useState, useEffect, useMemo } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_INTERVAL_MS = 60000

const SEVERITY_COLORS = {
  high:   { bg: '#7f1d1d', fg: '#fecaca' },
  medium: { bg: '#7c2d12', fg: '#fed7aa' },
  low:    { bg: '#713f12', fg: '#fef08a' },
}

const METRIC_LABELS = {
  total_cost_sek: 'Cost (SEK)',
  total_co2_kg: 'CO₂ (kg)',
  fleet_utilization_pct: 'Utilization %',
  total_distance_km: 'Distance (km)',
  total_tons: 'Tons',
}

function formatMetric(metric) {
  return METRIC_LABELS[metric] || metric
}

function formatPercent(n, digits = 1) {
  if (n == null) return '—'
  return `${Number(n).toFixed(digits)}%`
}

function round(n, d = 2) {
  if (n == null) return null
  return Math.round(n * 10 ** d) / 10 ** d
}

export function AnomalySummaryPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [zThreshold, setZThreshold] = useState(2.0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch(`${API_BASE}/persistence/anomaly-summary?z_threshold=${zThreshold}`)
      .then(r => r.json())
      .then(d => {
        if (!cancelled) {
          setData(d)
          setLoading(false)
        }
      })
      .catch(e => {
        if (!cancelled) {
          setError(e.message)
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [zThreshold])

  // Auto-refresh every 60s
  useEffect(() => {
    const id = setInterval(() => {
      fetch(`${API_BASE}/persistence/anomaly-summary?z_threshold=${zThreshold}`)
        .then(r => r.json())
        .then(d => setData(d))
        .catch(e => setError(e.message))
    }, REFRESH_INTERVAL_MS)
    return () => clearInterval(id)
  }, [zThreshold])

  // Compute max metric count for bar scaling
  const maxMetricCount = useMemo(() => {
    if (!data || !data.per_metric_counts) return 0
    return Math.max(1, ...Object.values(data.per_metric_counts))
  }, [data])

  if (loading && !data) {
    return (
      <div className="bg-slate-800 rounded-lg p-6">
        <h3 className="text-lg font-semibold text-slate-100 mb-4">
          🚨 Anomaly Summary
        </h3>
        <LoadingSpinner />
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-slate-800 rounded-lg p-6">
        <h3 className="text-lg font-semibold text-slate-100 mb-4">
          🚨 Anomaly Summary
        </h3>
        <div className="text-red-400 text-sm">Error: {error}</div>
      </div>
    )
  }

  if (!data) return null

  const insufficientHistory = data.insufficient_history
  const sevTotal = (data.per_severity_counts?.high || 0)
                 + (data.per_severity_counts?.medium || 0)
                 + (data.per_severity_counts?.low || 0)

  return (
    <div className="bg-slate-800 rounded-lg p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-slate-100">
          🚨 Anomaly Summary
        </h3>
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-400">z-threshold:</label>
          <select
            value={zThreshold}
            onChange={e => setZThreshold(parseFloat(e.target.value))}
            className="bg-slate-700 text-slate-100 text-xs rounded px-2 py-1"
          >
            <option value={1.5}>1.5</option>
            <option value={2.0}>2.0</option>
            <option value={2.5}>2.5</option>
            <option value={3.0}>3.0</option>
            <option value={3.5}>3.5</option>
          </select>
        </div>
      </div>

      {insufficientHistory && (
        <div className="bg-yellow-900/30 border border-yellow-700 text-yellow-300 text-xs rounded px-3 py-2 mb-4">
          ⚠️ Insufficient history ({data.n_total_cycles} cycles &lt; min_history={data.min_history}). Summary will populate once enough cycles exist.
        </div>
      )}

      {/* KPI cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div className="bg-slate-900 rounded-lg p-3">
          <div className="text-xs text-slate-400">Anomaly Rate</div>
          <div className="text-xl font-bold text-orange-400 mt-1">
            {formatPercent(data.anomaly_rate_pct)}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            {data.n_anomalous_cycles} / {data.n_total_cycles} cycles
          </div>
        </div>
        <div className="bg-slate-900 rounded-lg p-3">
          <div className="text-xs text-slate-400">Total Anomaly Events</div>
          <div className="text-xl font-bold text-red-400 mt-1">
            {data.total_anomaly_events}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            per-metric flags
          </div>
        </div>
        <div className="bg-slate-900 rounded-lg p-3">
          <div className="text-xs text-slate-400">Multi-anomaly Cycles</div>
          <div className="text-xl font-bold text-yellow-400 mt-1">
            {data.cycles_with_multiple_anomalies}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            {formatPercent(data.multi_anomaly_rate_pct)} of anomalies
          </div>
        </div>
        <div className="bg-slate-900 rounded-lg p-3">
          <div className="text-xs text-slate-400">Top Metric</div>
          <div className="text-base font-bold text-purple-400 mt-1 truncate">
            {data.most_common_metric ? formatMetric(data.most_common_metric) : '—'}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            {data.most_common_severity
              ? `severity: ${data.most_common_severity}`
              : 'no data'}
          </div>
        </div>
      </div>

      {/* Per-metric bar chart */}
      <div className="mb-4">
        <h4 className="text-sm font-semibold text-slate-300 mb-2">
          Anomalies by metric
        </h4>
        {data.top_anomalous_metrics.length === 0 ? (
          <div className="text-xs text-slate-500 italic">No anomalies detected at z={zThreshold}.</div>
        ) : (
          <div className="space-y-1.5">
            {data.top_anomalous_metrics.map(({ metric, count, pct_of_cycles }) => {
              const widthPct = Math.max(2, (count / maxMetricCount) * 100)
              return (
                <div key={metric} className="flex items-center gap-2 text-xs">
                  <div className="w-32 text-slate-300 truncate" title={metric}>
                    {formatMetric(metric)}
                  </div>
                  <div className="flex-1 bg-slate-900 rounded h-5 overflow-hidden">
                    <div
                      className="bg-gradient-to-r from-orange-600 to-red-600 h-full flex items-center justify-end pr-2"
                      style={{ width: `${widthPct}%` }}
                    >
                      <span className="text-white text-xs font-semibold">
                        {count}
                      </span>
                    </div>
                  </div>
                  <div className="w-16 text-right text-slate-400">
                    {formatPercent(pct_of_cycles, 1)}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Severity distribution */}
      {sevTotal > 0 && (
        <div>
          <h4 className="text-sm font-semibold text-slate-300 mb-2">
            Severity distribution
          </h4>
          <div className="flex gap-2">
            {['high', 'medium', 'low'].map(sev => {
              const count = data.per_severity_counts?.[sev] || 0
              const pct = sevTotal > 0 ? (count / sevTotal) * 100 : 0
              const colors = SEVERITY_COLORS[sev]
              return (
                <div
                  key={sev}
                  className="flex-1 rounded px-3 py-2"
                  style={{ backgroundColor: colors.bg, color: colors.fg }}
                  title={`${sev}: ${count} events (${pct.toFixed(1)}%)`}
                >
                  <div className="text-xs uppercase font-semibold">{sev}</div>
                  <div className="text-lg font-bold">{count}</div>
                  <div className="text-xs opacity-75">{pct.toFixed(1)}%</div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
