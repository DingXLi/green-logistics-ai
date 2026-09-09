/**
 * VehicleTimeseriesPanel - per-vehicle efficiency time-series (iter #65)
 *
 * 数据源:
 *   GET /api/persistence/vehicle-timeseries?metric=...
 *
 * 显示:
 * - Metric selector (7 efficiency metrics)
 * - Vehicle_id filter + sim_day window filter
 * - Per-vehicle summary table (mean/min/max/latest + trend badge)
 * - Time-series scatter/line chart (sim_day vs value, colored by vehicle)
 *
 * 用途: 让用户看到:
 *       - 每辆车 efficiency trend (improving / declining / stable)
 *       - 单 vehicle 跨 cycles 的 performance 变化
 *       - 异常 cycle 容易识别 (远偏离其他 cycle 的点)
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
  { key: 'distance_km', label: '📏 Distance', unit: 'km', lower_better: false },
  { key: 'n_stops', label: '📍 Stops', unit: '', lower_better: false },
]

function trendColor(trend) {
  if (trend === 'improving') return '#22c55e'  // green
  if (trend === 'declining') return '#ef4444'  // red
  return '#64748b'  // gray
}

export function VehicleTimeseriesPanel() {
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
      const params = new URLSearchParams({ metric: m, limit: '200' })
      if (vid !== '') params.set('vehicle_id', vid)
      if (sd !== '') params.set('since_sim_day', String(sd))
      if (ud !== '') params.set('until_sim_day', String(ud))
      const res = await fetch(`${API_BASE}/persistence/vehicle-timeseries?${params}`)
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
    return <LoadingSpinner size="md" label="Loading vehicle timeseries…" />
  }

  if (error || !data) {
    return (
      <div className="card vehicle-timeseries-panel">
        <h3>🚐 Vehicle Timeseries</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No vehicle data yet.'}
        </div>
      </div>
    )
  }

  const metricDef = METRICS.find((m) => m.key === metric)
  const summary = data.per_vehicle_summary || {}
  const summaryEntries = Object.entries(summary).sort((a, b) =>
    b[1].latest_value - a[1].latest_value
  )

  return (
    <div className="card vehicle-timeseries-panel">
      <div className="card-header-row">
        <h3>🚐 Vehicle Timeseries</h3>
        <div className="card-controls">
          <span className="card-badge">
            {data.n_routes_evaluated} routes evaluated ·{' '}
            {summaryEntries.length} vehicles
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

      {summaryEntries.length === 0 ? (
        <div className="empty-state">No vehicles in this window.</div>
      ) : (
        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>Vehicle</th>
                <th className="numeric">Routes</th>
                <th className="numeric">Mean</th>
                <th className="numeric">Min</th>
                <th className="numeric">Max</th>
                <th className="numeric">Latest</th>
                <th>Trend</th>
                <th className="numeric">First → Last day</th>
              </tr>
            </thead>
            <tbody>
              {summaryEntries.map(([vid, s]) => (
                <tr key={vid}>
                  <td className="mono"><strong>{vid}</strong></td>
                  <td className="numeric">{s.n_routes}</td>
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