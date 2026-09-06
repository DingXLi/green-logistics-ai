/**
 * PredictionAccuracyPanel - per-day forecast accuracy by lead time (iter #58)
 *
 * 数据源:
 *   GET /api/persistence/prediction-accuracy-by-day
 *
 * 显示:
 * - Metric selector + method selector
 * - Custom lead-time buckets (CSV input: "1-1,2-7,8-30")
 * - Per-day table: day, n_predictions, n_evaluated, n_pending, MAPE
 * - Per-lead-time MAPE breakdown
 *
 * 用途: 让用户看到:
 *       - 预测准确度随时间变化 (per-day MAPE trend)
 *       - 不同 lead time 的准确度差异 (1-day-ahead vs 7-day-ahead)
 *       - pending predictions (待评估) 数量
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const METRICS = ['', 'cost_sek', 'co2_kg', 'util_pct', 'matches']
const METHODS = ['', 'linear', 'moving_average', 'exponential_smoothing']

const DEFAULT_BUCKETS = '1-1,2-3,4-7,8-14,15-30'

export function PredictionAccuracyPanel() {
  const [metric, setMetric] = useState('')
  const [method, setMethod] = useState('')
  const [bucketsCsv, setBucketsCsv] = useState(DEFAULT_BUCKETS)
  const [sinceDay, setSinceDay] = useState('')
  const [untilDay, setUntilDay] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async () => {
    try {
      const params = new URLSearchParams()
      if (metric) params.set('metric', metric)
      if (method) params.set('method', method)
      if (bucketsCsv && bucketsCsv !== DEFAULT_BUCKETS) {
        params.set('lead_time_buckets', bucketsCsv)
      }
      if (sinceDay !== '') params.set('since_created_day', String(sinceDay))
      if (untilDay !== '') params.set('until_created_day', String(untilDay))
      const url = `${API_BASE}/persistence/prediction-accuracy-by-day${params.toString() ? '?' + params : ''}`
      const res = await fetch(url)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, REFRESH_MS)
    return () => clearInterval(id)
  }, [metric, method, bucketsCsv, sinceDay, untilDay])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading prediction accuracy…" />
  }

  if (error || !data) {
    return (
      <div className="card prediction-accuracy-panel">
        <h3>🎯 Prediction Accuracy (iter #58)</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No forecast predictions yet. Run forecasts to populate.'}
        </div>
      </div>
    )
  }

  const days = data.by_day || []
  const buckets = data.lead_time_buckets || []
  const overall = data.overall || {}

  return (
    <div className="card prediction-accuracy-panel">
      <div className="card-header-row">
        <h3>🎯 Prediction Accuracy by Day</h3>
        <div className="card-controls">
          <span className="card-badge">
            {overall.n_evaluated || 0} evaluated ·{' '}
            {overall.n_pending || 0} pending ·{' '}
            MAPE {overall.overall_mape_pct ?? '—'}%
          </span>
        </div>
      </div>

      <div className="filter-row">
        <label>Metric:</label>
        <select
          className="filter-select"
          value={metric}
          onChange={(e) => setMetric(e.target.value)}
        >
          {METRICS.map((m) => (
            <option key={m || 'all'} value={m}>{m || 'all'}</option>
          ))}
        </select>

        <label>Method:</label>
        <select
          className="filter-select"
          value={method}
          onChange={(e) => setMethod(e.target.value)}
        >
          {METHODS.map((m) => (
            <option key={m || 'all'} value={m}>{m || 'all'}</option>
          ))}
        </select>

        <label>Buckets (CSV):</label>
        <input
          type="text"
          className="filter-input"
          value={bucketsCsv}
          placeholder="1-1,2-3,4-7,8-14,15-30"
          onChange={(e) => setBucketsCsv(e.target.value)}
        />

        <label>Since day:</label>
        <input
          type="number"
          className="filter-input filter-input-narrow"
          placeholder="∞"
          value={sinceDay}
          onChange={(e) => setSinceDay(e.target.value)}
        />

        <label>Until day:</label>
        <input
          type="number"
          className="filter-input filter-input-narrow"
          placeholder="∞"
          value={untilDay}
          onChange={(e) => setUntilDay(e.target.value)}
        />
      </div>

      {days.length === 0 ? (
        <div className="empty-state">
          No predictions yet for the selected filters.
        </div>
      ) : (
        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>Created day</th>
                <th className="numeric">Predictions</th>
                <th className="numeric">Evaluated</th>
                <th className="numeric">Pending</th>
                <th className="numeric">Overall MAPE</th>
                {buckets.map((b) => (
                  <th key={b.label} className="numeric">
                    Lead {b.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {days.map((d) => (
                <tr key={d.created_at_sim_day}>
                  <td className="mono">D{d.created_at_sim_day}</td>
                  <td className="numeric">{d.n_predictions}</td>
                  <td className="numeric">{d.n_evaluated}</td>
                  <td className="numeric">{d.n_pending}</td>
                  <td className="numeric metric-value">
                    {d.overall_mape_pct !== null ? `${d.overall_mape_pct.toFixed(1)}%` : '—'}
                  </td>
                  {buckets.map((b) => {
                    const lt = d.by_lead_time?.[b.label]
                    return (
                      <td key={b.label} className="numeric">
                        {lt && lt.n_evaluated > 0
                          ? `${lt.mape_pct?.toFixed(1)}%`
                          : '—'}
                        <span className="metric-unit">
                          {lt && lt.n_evaluated > 0 ? ` (${lt.n_evaluated})` : ''}
                        </span>
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-footnote">
        Bucket label format: <code>lo-hi</code> = lead_time in days (inclusive).
        Default: {DEFAULT_BUCKETS}.{' '}
        iter #58 · auto-refresh 60s
      </div>
    </div>
  )
}
