/**
 * TopDemandsPanel - top demands by fulfillment (iter #57)
 *
 * 数据源:
 *   GET /api/persistence/top-demands?metric=...
 *
 * 显示:
 * - Metric selector (5 fulfillment metrics)
 * - Material filter
 * - Min required tons filter
 * - Top N demands with rank, ID, value, totals
 *
 * 用途: 让用户看到:
 *       - 哪些 demand 被充分满足 (high fulfillment_rate)
 *       - 哪些 demand 有 unmet gap (high unmet_demand_tons)
 *       - 哪些 demand 难以触及 (high avg_match_distance_km)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const METRICS = [
  { key: 'fulfillment_rate', label: '✅ Fulfillment', unit: 'ratio', lower_better: false },
  { key: 'total_matched_tons', label: '📦 Matched', unit: 't', lower_better: false },
  { key: 'unmet_demand_tons', label: '⚠️ Unmet', unit: 't', lower_better: true },
  { key: 'match_rate', label: '🔄 Match rate', unit: 'm/cyc', lower_better: false },
  { key: 'avg_match_distance_km', label: '📏 Avg dist', unit: 'km', lower_better: true },
]

export function TopDemandsPanel() {
  const [metric, setMetric] = useState('fulfillment_rate')
  const [materialFilter, setMaterialFilter] = useState('')
  const [minRequired, setMinRequired] = useState(0)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async (m = metric, mat = materialFilter, mr = minRequired) => {
    try {
      const params = new URLSearchParams({ metric: m, limit: '15' })
      if (mat) params.set('material_type', mat)
      if (mr > 0) params.set('min_required_tons', String(mr))
      const res = await fetch(`${API_BASE}/persistence/top-demands?${params}`)
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
      fetchData(metric, materialFilter, minRequired)
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    fetchData(metric, materialFilter, minRequired)
  }, [metric, materialFilter, minRequired])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading top demands…" />
  }

  if (error || !data) {
    return (
      <div className="card top-demands-panel">
        <h3>📋 Top Demands by Fulfillment (iter #57)</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No demand data yet. Run simulations to populate.'}
        </div>
      </div>
    )
  }

  const metricDef = METRICS.find((m) => m.key === metric)
  const demands = data.top_demands || []

  return (
    <div className="card top-demands-panel">
      <div className="card-header-row">
        <h3>📋 Top Demands by Fulfillment</h3>
        <div className="card-controls">
          <span className="card-badge">
            {data.n_demands_evaluated} demands evaluated ·{' '}
            {data.n_demands_returned} shown
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
          className="filter-input"
          placeholder="filter material…"
          value={materialFilter}
          onChange={(e) => setMaterialFilter(e.target.value)}
        />

        <label>Min required (t):</label>
        <input
          type="number"
          className="filter-input filter-input-narrow"
          min="0"
          value={minRequired}
          onChange={(e) => setMinRequired(Number(e.target.value) || 0)}
        />
      </div>

      {demands.length === 0 ? (
        <div className="empty-state">
          No demands match the current filter. Try lowering min_required or
          clearing the material filter.
        </div>
      ) : (
        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Demand ID</th>
                <th>Material</th>
                <th>{metricDef?.label || metric}</th>
                <th className="numeric">Matches</th>
                <th className="numeric">Required (t)</th>
                <th className="numeric">Matched (t)</th>
                <th className="numeric">Fill rate</th>
                <th className="numeric">Avg dist (km)</th>
              </tr>
            </thead>
            <tbody>
              {demands.map((d, idx) => (
                <tr key={d.demand_id}>
                  <td className="rank-cell">{idx + 1}</td>
                  <td className="mono">{d.demand_id}</td>
                  <td>{d.material_type}</td>
                  <td className="numeric metric-value">
                    {d.value !== null && d.value !== undefined
                      ? d.value.toFixed(2)
                      : '—'}{' '}
                    <span className="metric-unit">{metricDef?.unit || ''}</span>
                  </td>
                  <td className="numeric">{d.n_matches}</td>
                  <td className="numeric">{d.total_required_tons?.toFixed(1)}</td>
                  <td className="numeric">{d.total_matched_tons?.toFixed(1)}</td>
                  <td className="numeric">{(d.fulfillment_rate * 100)?.toFixed(0)}%</td>
                  <td className="numeric">{d.avg_match_distance_km?.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-footnote">
        Direction: <strong>{data.direction === 'lower_is_better' ? '↓ lower = better' : '↑ higher = better'}</strong>
        {' · '}
        iter #57 · auto-refresh 60s
      </div>
    </div>
  )
}
