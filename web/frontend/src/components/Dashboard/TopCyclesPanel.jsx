/**
 * TopCyclesPanel - top optimization cycles by efficiency (iter #56)
 *
 * 数据源:
 *   GET /api/persistence/top-cycles?metric=...
 *
 * 显示:
 * - Metric selector (7 efficiency metrics)
 * - Sim_day window filter (since / until)
 * - Min-matches filter
 * - Top N cycles with rank, ID, value, sim_day, totals
 *
 * 用途: 让用户看到:
 *       - 哪些 cycle 最 green (low co2_per_ton)
 *       - 哪些 cycle 最 cost-efficient (low cost_per_ton)
 *       - 哪些 cycle fleet 最满载 (high fleet_utilization)
 *       - 哪些 cycle 最 productive (high tons_per_cycle)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const METRICS = [
  { key: 'co2_per_ton', label: '🌱 CO₂ / ton', unit: 'kg/t', lower_better: true },
  { key: 'cost_per_ton', label: '💰 Cost / ton', unit: 'SEK/t', lower_better: true },
  { key: 'co2_per_km', label: '🌱 CO₂ / km', unit: 'kg/km', lower_better: true },
  { key: 'cost_per_km', label: '💰 Cost / km', unit: 'SEK/km', lower_better: true },
  { key: 'fleet_utilization', label: '⚡ Fleet util', unit: '%', lower_better: false },
  { key: 'match_rate_vs_offers', label: '✅ Match rate', unit: 'm/off', lower_better: false },
  { key: 'tons_per_cycle', label: '📦 Tons / cycle', unit: 't', lower_better: false },
]

export function TopCyclesPanel() {
  const [metric, setMetric] = useState('co2_per_ton')
  const [sinceDay, setSinceDay] = useState('')
  const [untilDay, setUntilDay] = useState('')
  const [minMatches, setMinMatches] = useState(1)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async (m = metric, sd = sinceDay, ud = untilDay, mm = minMatches) => {
    try {
      const params = new URLSearchParams({ metric: m, limit: '15', min_matches: String(mm) })
      if (sd !== '') params.set('since_sim_day', String(sd))
      if (ud !== '') params.set('until_sim_day', String(ud))
      const res = await fetch(`${API_BASE}/persistence/top-cycles?${params}`)
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
      fetchData(metric, sinceDay, untilDay, minMatches)
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    fetchData(metric, sinceDay, untilDay, minMatches)
  }, [metric, sinceDay, untilDay, minMatches])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading top cycles…" />
  }

  if (error || !data) {
    return (
      <div className="card top-cycles-panel">
        <h3>🔄 Top Cycles (iter #56)</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No cycle data yet. Run simulations to populate.'}
        </div>
      </div>
    )
  }

  const metricDef = METRICS.find((m) => m.key === metric)
  const cycles = data.top_cycles || []

  return (
    <div className="card top-cycles-panel">
      <div className="card-header-row">
        <h3>🔄 Top Cycles by Efficiency</h3>
        <div className="card-controls">
          <span className="card-badge">
            {data.n_cycles_evaluated} cycles evaluated ·{' '}
            {data.n_cycles_returned} shown
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

        <label>Min matches:</label>
        <input
          type="number"
          className="filter-input filter-input-narrow"
          min="0"
          value={minMatches}
          onChange={(e) => setMinMatches(Number(e.target.value) || 0)}
        />
      </div>

      {cycles.length === 0 ? (
        <div className="empty-state">
          No cycles match the current filter. Try widening the sim_day window or
          lowering min_matches.
        </div>
      ) : (
        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Cycle ID</th>
                <th className="numeric">Sim day</th>
                <th>{metricDef?.label || metric}</th>
                <th className="numeric">Matches</th>
                <th className="numeric">Tons</th>
                <th className="numeric">CO₂ (kg)</th>
                <th className="numeric">Cost (SEK)</th>
                <th className="numeric">Util (%)</th>
              </tr>
            </thead>
            <tbody>
              {cycles.map((c, idx) => (
                <tr key={c.cycle_id}>
                  <td className="rank-cell">{idx + 1}</td>
                  <td className="mono">{c.cycle_id}</td>
                  <td className="numeric">{c.sim_day}</td>
                  <td className="numeric metric-value">
                    {c.value !== null && c.value !== undefined
                      ? c.value.toFixed(2)
                      : '—'}{' '}
                    <span className="metric-unit">{metricDef?.unit || ''}</span>
                  </td>
                  <td className="numeric">{c.n_matches}</td>
                  <td className="numeric">{c.total_tons?.toFixed(1)}</td>
                  <td className="numeric">{c.total_co2_kg?.toFixed(1)}</td>
                  <td className="numeric">{c.total_cost_sek?.toFixed(0)}</td>
                  <td className="numeric">{c.fleet_utilization_pct?.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-footnote">
        Direction: <strong>{data.direction === 'lower_is_better' ? '↓ lower = better' : '↑ higher = better'}</strong>
        {' · '}
        Window: {data.sim_day_window?.since_sim_day ?? '∞'} →{' '}
        {data.sim_day_window?.until_sim_day ?? '∞'}
        {' · '}
        iter #56 · auto-refresh 60s
      </div>
    </div>
  )
}
