/**
 * TopRoutesPanel - top routes (per cycle × per vehicle) by efficiency (iter #63)
 *
 * 数据源:
 *   GET /api/persistence/top-routes?metric=...
 *
 * 显示:
 * - Metric selector (7 efficiency metrics for route rows)
 * - Vehicle_id filter
 * - Sim_day window filter (since / until)
 * - Top N routes with rank, route_id, vehicle_id, cycle_id, value + context
 *
 * 用途: 让用户看到:
 *       - 哪些 cycle × vehicle 组合最 green (low co2_per_km)
 *       - 哪些 route 最便宜 (low cost_per_km)
 *       - 哪些 route 最快 (high speed_km_per_hour)
 *       - 哪些 route 最满载 (high tons_per_km)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const METRICS = [
  { key: 'co2_per_km', label: '🌱 CO₂ / km', unit: 'kg/km', lower_better: true },
  { key: 'co2_per_hour', label: '🌱 CO₂ / hr', unit: 'kg/hr', lower_better: true },
  { key: 'cost_per_km', label: '💰 Cost / km', unit: 'SEK/km', lower_better: true },
  { key: 'cost_per_hour', label: '💰 Cost / hr', unit: 'SEK/hr', lower_better: true },
  { key: 'speed_km_per_hour', label: '⚡ Speed', unit: 'km/hr', lower_better: false },
  { key: 'distance', label: '📏 Distance', unit: 'km', lower_better: false },
  { key: 'tons_per_km', label: '📦 Tons / km', unit: 't/km', lower_better: false },
]

export function TopRoutesPanel() {
  const [metric, setMetric] = useState('co2_per_km')
  const [vehicleId, setVehicleId] = useState('')
  const [sinceDay, setSinceDay] = useState('')
  const [untilDay, setUntilDay] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async (
    m = metric, vid = vehicleId, sd = sinceDay, ud = untilDay
  ) => {
    try {
      const params = new URLSearchParams({ metric: m, limit: '15' })
      if (vid !== '') params.set('vehicle_id', vid)
      if (sd !== '') params.set('since_sim_day', String(sd))
      if (ud !== '') params.set('until_sim_day', String(ud))
      const res = await fetch(`${API_BASE}/persistence/top-routes?${params}`)
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
      fetchData(metric, vehicleId, sinceDay, untilDay)
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    fetchData(metric, vehicleId, sinceDay, untilDay)
  }, [metric, vehicleId, sinceDay, untilDay])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading top routes…" />
  }

  if (error || !data) {
    return (
      <div className="card top-routes-panel">
        <h3>🛣️ Top Routes by Efficiency</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No route data yet. Run simulations to populate.'}
        </div>
      </div>
    )
  }

  const metricDef = METRICS.find((m) => m.key === metric)
  const routes = data.top_routes || []

  return (
    <div className="card top-routes-panel">
      <div className="card-header-row">
        <h3>🛣️ Top Routes by Efficiency</h3>
        <div className="card-controls">
          <span className="card-badge">
            {data.n_routes_evaluated} routes evaluated ·{' '}
            {data.n_routes_returned} shown
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

        <label>Vehicle:</label>
        <input
          type="text"
          className="filter-input filter-input-narrow"
          placeholder="any"
          value={vehicleId}
          onChange={(e) => setVehicleId(e.target.value)}
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

      {routes.length === 0 ? (
        <div className="empty-state">
          No routes match the current filter. Try widening the sim_day window
          or clearing the vehicle filter.
        </div>
      ) : (
        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Route ID</th>
                <th>Vehicle</th>
                <th>Cycle</th>
                <th className="numeric">Day</th>
                <th>{metricDef?.label || metric}</th>
                <th className="numeric">Dist (km)</th>
                <th className="numeric">Dur (h)</th>
                <th className="numeric">Cost (SEK)</th>
                <th className="numeric">CO₂ (kg)</th>
                <th className="numeric">Stops</th>
                <th className="numeric">Tons</th>
              </tr>
            </thead>
            <tbody>
              {routes.map((r) => (
                <tr key={r.route_id}>
                  <td className="rank-cell">{(routes.indexOf(r) + 1)}</td>
                  <td className="mono">#{r.route_id}</td>
                  <td className="mono">{r.vehicle_id}</td>
                  <td className="mono">{r.cycle_id}</td>
                  <td className="numeric">{r.sim_day}</td>
                  <td className="numeric metric-value">
                    {r.value !== null && r.value !== undefined
                      ? r.value.toFixed(3)
                      : '—'}{' '}
                    <span className="metric-unit">{metricDef?.unit || ''}</span>
                  </td>
                  <td className="numeric">{r.distance_km?.toFixed(1)}</td>
                  <td className="numeric">{r.duration_hours?.toFixed(2)}</td>
                  <td className="numeric">{r.cost_sek?.toFixed(0)}</td>
                  <td className="numeric">{r.co2_kg?.toFixed(2)}</td>
                  <td className="numeric">{r.n_stops}</td>
                  <td className="numeric">{r.cycle_tons?.toFixed(1)}</td>
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