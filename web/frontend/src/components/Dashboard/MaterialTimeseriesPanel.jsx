/**
 * MaterialTimeseriesPanel - per-material efficiency time-series (iter #65)
 *
 * 数据源:
 *   GET /api/persistence/material-timeseries?metric=...
 *
 * 显示:
 * - Metric selector (6 volume / efficiency metrics)
 * - Material filter + sim_day window filter
 * - Per-material summary table (mean/min/max/latest + trend badge)
 *
 * 用途: 让用户看到:
 *       - 每种 material 的 per-cycle volume / efficiency 变化
 *       - material-level trend (improving / declining / stable)
 *       - 异常 material cycles (远偏离)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const METRICS = [
  { key: 'matched_tons', label: '📦 Matched tons', unit: 't', lower_better: false },
  { key: 'n_matches', label: '🤝 Matches', unit: '', lower_better: false },
  { key: 'avg_match_tons', label: '⚖️ Tons / match', unit: 't', lower_better: false },
  { key: 'co2_per_ton', label: '🌱 CO₂ / ton', unit: 'kg/t', lower_better: true },
  { key: 'cost_per_ton', label: '💰 Cost / ton', unit: 'SEK/t', lower_better: true },
  { key: 'match_rate', label: '✅ Match rate', unit: 'm/off', lower_better: false },
]

function trendColor(trend) {
  if (trend === 'improving') return '#22c55e'
  if (trend === 'declining') return '#ef4444'
  return '#64748b'
}

export function MaterialTimeseriesPanel() {
  const [metric, setMetric] = useState('matched_tons')
  const [material, setMaterial] = useState('')
  const [sinceDay, setSinceDay] = useState('')
  const [untilDay, setUntilDay] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async (
    m = metric, mat = material, sd = sinceDay, ud = untilDay
  ) => {
    try {
      const params = new URLSearchParams({ metric: m, limit: '200' })
      if (mat !== '') params.set('material_type', mat)
      if (sd !== '') params.set('since_sim_day', String(sd))
      if (ud !== '') params.set('until_sim_day', String(ud))
      const res = await fetch(`${API_BASE}/persistence/material-timeseries?${params}`)
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
    const id = setInterval(() => {
      fetchData(metric, material, sinceDay, untilDay)
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    fetchData(metric, material, sinceDay, untilDay)
  }, [metric, material, sinceDay, untilDay])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading material timeseries…" />
  }

  if (error || !data) {
    return (
      <div className="card material-timeseries-panel">
        <h3>📦 Material Timeseries</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No material data yet.'}
        </div>
      </div>
    )
  }

  const summary = data.per_material_summary || {}
  const summaryEntries = Object.entries(summary).sort((a, b) =>
    b[1].latest_value - a[1].latest_value
  )

  return (
    <div className="card material-timeseries-panel">
      <div className="card-header-row">
        <h3>📦 Material Timeseries</h3>
        <div className="card-controls">
          <span className="card-badge">
            {data.n_rows_evaluated} rows · {summaryEntries.length} materials
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
            <option key={m.key} value={m.key}>{m.label}</option>
          ))}
        </select>

        <label>Material:</label>
        <input
          type="text"
          className="filter-input filter-input-narrow"
          placeholder="any"
          value={material}
          onChange={(e) => setMaterial(e.target.value)}
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

      {summaryEntries.length === 0 ? (
        <div className="empty-state">No materials in this window.</div>
      ) : (
        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>Material</th>
                <th className="numeric">Cycles</th>
                <th className="numeric">Mean</th>
                <th className="numeric">Min</th>
                <th className="numeric">Max</th>
                <th className="numeric">Latest</th>
                <th>Trend</th>
                <th className="numeric">First → Last day</th>
              </tr>
            </thead>
            <tbody>
              {summaryEntries.map(([mat, s]) => (
                <tr key={mat}>
                  <td className="mono"><strong>{mat}</strong></td>
                  <td className="numeric">{s.n_cycles}</td>
                  <td className="numeric">{s.mean_value?.toFixed(3)}</td>
                  <td className="numeric">{s.min_value?.toFixed(3)}</td>
                  <td className="numeric">{s.max_value?.toFixed(3)}</td>
                  <td className="numeric metric-value">{s.latest_value?.toFixed(3)}</td>
                  <td>
                    <span
                      className="trend-badge"
                      style={{
                        backgroundColor: trendColor(s.trend),
                        color: 'white',
                      }}
                    >
                      {s.trend === 'improving' ? '↗ improving' :
                       s.trend === 'declining' ? '↘ declining' : '→ stable'}
                    </span>
                  </td>
                  <td className="numeric">
                    {s.first_sim_day} → {s.last_sim_day}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-footnote">
        Direction:{' '}
        <strong>
          {data.direction === 'lower_is_better' ? '↓ lower = better' : '↑ higher = better'}
        </strong>
        {' · '}
        Window: {data.filter?.since_sim_day ?? '∞'} →{' '}
        {data.filter?.until_sim_day ?? '∞'}
        {' · '}
        iter #65 · auto-refresh 60s
      </div>
    </div>
  )
}