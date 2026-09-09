/**
 * TopMaterialsPanel - top materials by volume / efficiency (iter #63)
 *
 * 数据源:
 *   GET /api/persistence/top-materials?metric=...
 *
 * 显示:
 * - Metric selector (11 volume/efficiency metrics)
 * - Sim_day window filter (since / until)
 * - Top N materials with rank, name, value + context columns
 *
 * 用途: 让用户看到:
 *       - 哪些 material 占主导 (high total_matched_tons)
 *       - 哪些 material 供需失衡 (low match_rate)
 *       - 哪些 material 最 clean (low co2_per_ton)
 *       - 哪些 material 被请求最多 (high total_demand_tons)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const METRICS = [
  { key: 'total_matched_tons', label: '📦 Matched tons', unit: 't', lower_better: false },
  { key: 'total_supply_tons', label: '📥 Supply tons', unit: 't', lower_better: false },
  { key: 'total_demand_tons', label: '📤 Demand tons', unit: 't', lower_better: false },
  { key: 'n_matches', label: '🤝 Matches', unit: '', lower_better: false },
  { key: 'n_supply_offers', label: '🏭 Offers', unit: '', lower_better: false },
  { key: 'n_demand_requests', label: '🛒 Demands', unit: '', lower_better: false },
  { key: 'match_rate', label: '✅ Match rate', unit: 'm/off', lower_better: false },
  { key: 'avg_tons_per_match', label: '⚖️ Tons / match', unit: 't', lower_better: false },
  { key: 'co2_per_ton', label: '🌱 CO₂ / ton', unit: 'kg/t', lower_better: true },
  { key: 'cost_per_ton', label: '💰 Cost / ton', unit: 'SEK/t', lower_better: true },
  { key: 'avg_distance_km', label: '📏 Avg distance', unit: 'km', lower_better: true },
]

export function TopMaterialsPanel() {
  const [metric, setMetric] = useState('total_matched_tons')
  const [sinceDay, setSinceDay] = useState('')
  const [untilDay, setUntilDay] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async (m = metric, sd = sinceDay, ud = untilDay) => {
    try {
      const params = new URLSearchParams({ metric: m, limit: '15' })
      if (sd !== '') params.set('since_sim_day', String(sd))
      if (ud !== '') params.set('until_sim_day', String(ud))
      const res = await fetch(`${API_BASE}/persistence/top-materials?${params}`)
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
      fetchData(metric, sinceDay, untilDay)
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    fetchData(metric, sinceDay, untilDay)
  }, [metric, sinceDay, untilDay])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading top materials…" />
  }

  if (error || !data) {
    return (
      <div className="card top-materials-panel">
        <h3>📦 Top Materials by Volume</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No material data yet. Run simulations to populate.'}
        </div>
      </div>
    )
  }

  const metricDef = METRICS.find((m) => m.key === metric)
  const materials = data.top_materials || []

  return (
    <div className="card top-materials-panel">
      <div className="card-header-row">
        <h3>📦 Top Materials by Volume</h3>
        <div className="card-controls">
          <span className="card-badge">
            {data.n_materials_evaluated} materials evaluated ·{' '}
            {data.n_materials_returned} shown
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

      {materials.length === 0 ? (
        <div className="empty-state">
          No materials match the current filter. Try widening the sim_day window.
        </div>
      ) : (
        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Material</th>
                <th>{metricDef?.label || metric}</th>
                <th className="numeric">Matched (t)</th>
                <th className="numeric">Supply (t)</th>
                <th className="numeric">Demand (t)</th>
                <th className="numeric">Matches</th>
                <th className="numeric">Match rate</th>
                <th className="numeric">Fulfill %</th>
                <th className="numeric">CO₂ (kg)</th>
              </tr>
            </thead>
            <tbody>
              {materials.map((m, idx) => (
                <tr key={m.material_type}>
                  <td className="rank-cell">{idx + 1}</td>
                  <td className="mono"><strong>{m.material_type}</strong></td>
                  <td className="numeric metric-value">
                    {m.value !== null && m.value !== undefined
                      ? Number.isInteger(m.value) ? m.value : m.value.toFixed(3)
                      : '—'}{' '}
                    <span className="metric-unit">{metricDef?.unit || ''}</span>
                  </td>
                  <td className="numeric">{m.total_matched_tons?.toFixed(1)}</td>
                  <td className="numeric">{m.total_supply_tons?.toFixed(1)}</td>
                  <td className="numeric">{m.total_demand_tons?.toFixed(1)}</td>
                  <td className="numeric">{m.n_matches}</td>
                  <td className="numeric">
                    {m.match_rate !== null ? m.match_rate.toFixed(2) : '—'}
                  </td>
                  <td className="numeric">{m.demand_fulfillment_pct?.toFixed(1)}</td>
                  <td className="numeric">{m.total_co2_kg?.toFixed(1)}</td>
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
        iter #63 · auto-refresh 60s
      </div>
    </div>
  )
}