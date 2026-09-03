/**
 * VehicleStats.jsx — iter #41
 *
 * Visualizes per-vehicle historical aggregates from
 * /api/persistence/vehicle-stats. Operators can see which
 * vehicles are most active, most efficient, and identify
 * outliers (overworked or underutilized vehicles).
 */
import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend, Cell,
} from 'recharts'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

function fmtNum(v, suffix = '') {
  if (v === null || v === undefined) return '—'
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(1)}k${suffix}`
  return `${v.toFixed(1)}${suffix}`
}

function fmtCost(v) {
  if (v === null || v === undefined) return '—'
  return `${Math.round(v).toLocaleString()} SEK`
}

function fmtPerKm(v) {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(2)} SEK/km`
}

function fmtPerKmKg(v) {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(2)} kg/km`
}

export function VehicleStats() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [sortBy, setSortBy] = useState('total_distance_km')
  const [topN, setTopN] = useState(15)

  const fetchStats = () => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams({ limit: String(topN) })
    fetch(`${API_BASE}/persistence/vehicle-stats?${params}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(d => {
        setData(d)
        setLoading(false)
      })
      .catch(e => {
        setError(e.message)
        setLoading(false)
      })
  }

  useEffect(() => {
    fetchStats()
    const id = setInterval(fetchStats, 60000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topN])

  if (loading && !data) {
    return (
      <div className="vehicle-stats-panel">
        <div className="vs-header">
          <h3>🚚 Vehicle Historical Stats (iter #41)</h3>
        </div>
        <LoadingSpinner label="Loading vehicle stats..." />
      </div>
    )
  }

  if (error) {
    return (
      <div className="vehicle-stats-panel">
        <div className="vs-header">
          <h3>🚚 Vehicle Historical Stats (iter #41)</h3>
        </div>
        <div className="vs-error">Error: {error}</div>
      </div>
    )
  }

  const vehicles = data?.vehicles || []
  // Sort according to sortBy
  const sorted = [...vehicles].sort((a, b) => (b[sortBy] ?? 0) - (a[sortBy] ?? 0))
  const topVehicles = sorted.slice(0, topN)

  // KPI cards (aggregate over ALL vehicles, not just topN)
  const totalDistance = vehicles.reduce((s, v) => s + (v.total_distance_km || 0), 0)
  const totalCost = vehicles.reduce((s, v) => s + (v.total_cost_sek || 0), 0)
  const totalCo2 = vehicles.reduce((s, v) => s + (v.total_co2_kg || 0), 0)
  const totalRoutes = vehicles.reduce((s, v) => s + (v.n_routes || 0), 0)
  const avgCostPerKm = totalDistance > 0 ? totalCost / totalDistance : 0
  const avgCo2PerKm = totalDistance > 0 ? totalCo2 / totalDistance : 0

  return (
    <div className="vehicle-stats-panel">
      <div className="vs-header">
        <h3>🚚 Vehicle Historical Stats <span className="iter-badge">iter #41</span></h3>
        <button className="refresh-btn" onClick={fetchStats} disabled={loading}>
          {loading ? 'Refreshing...' : '🔄 Refresh'}
        </button>
      </div>

      <div className="vs-controls">
        <label className="vs-label">
          <span>Sort by:</span>
          <select value={sortBy} onChange={e => setSortBy(e.target.value)} className="vs-select">
            <option value="total_distance_km">Total distance</option>
            <option value="total_cost_sek">Total cost</option>
            <option value="total_co2_kg">Total CO₂</option>
            <option value="n_routes"># Routes</option>
            <option value="avg_cost_per_km_sek">Cost/km</option>
            <option value="avg_co2_per_km_kg">CO₂/km</option>
            <option value="last_sim_day">Last used (recency)</option>
          </select>
        </label>
        <label className="vs-label">
          <span>Show top:</span>
          <select value={topN} onChange={e => setTopN(parseInt(e.target.value, 10))} className="vs-select">
            <option value={5}>5</option>
            <option value={10}>10</option>
            <option value={15}>15</option>
            <option value={30}>30</option>
            <option value={50}>50</option>
          </select>
        </label>
      </div>

      {vehicles.length === 0 ? (
        <div className="vs-empty">
          No vehicle data yet — run a cycle with the simulator to populate.
        </div>
      ) : (
        <>
          <div className="cs-kpi-row">
            <div className="cs-kpi">
              <div className="kpi-label">Vehicles</div>
              <div className="kpi-value">{vehicles.length}</div>
              <div className="kpi-sub">distinct IDs in DB</div>
            </div>
            <div className="cs-kpi">
              <div className="kpi-label">Total Distance</div>
              <div className="kpi-value">{fmtNum(totalDistance, ' km')}</div>
              <div className="kpi-sub">{totalRoutes} routes</div>
            </div>
            <div className="cs-kpi">
              <div className="kpi-label">Total Cost</div>
              <div className="kpi-value">{fmtCost(totalCost)}</div>
              <div className="kpi-sub">{fmtPerKm(avgCostPerKm)} avg</div>
            </div>
            <div className="cs-kpi">
              <div className="kpi-label">Total CO₂</div>
              <div className="kpi-value">{fmtNum(totalCo2, ' kg')}</div>
              <div className="kpi-sub">{fmtPerKmKg(avgCo2PerKm)} avg</div>
            </div>
          </div>

          <div className="vs-chart-wrap" style={{ height: 320 }}>
            <ResponsiveContainer>
              <BarChart data={topVehicles} margin={{ top: 20, right: 30, left: 20, bottom: 60 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                <XAxis
                  dataKey="vehicle_id"
                  stroke="#888"
                  angle={-45}
                  textAnchor="end"
                  height={70}
                />
                <YAxis stroke="#888" tickFormatter={v => fmtNum(v)} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1e1e1e', border: '1px solid #444' }}
                  formatter={(v, name) => [fmtNum(v), name]}
                />
                <Legend />
                <Bar dataKey="total_distance_km" name="Distance (km)" fill="#3b82f6">
                  {topVehicles.map((_, i) => (
                    <Cell key={i} fill={i === 0 ? '#f59e0b' : '#3b82f6'} />
                  ))}
                </Bar>
                <Bar dataKey="total_cost_sek" name="Cost (SEK)" fill="#ef4444" />
                <Bar dataKey="total_co2_kg" name="CO₂ (kg)" fill="#10b981" />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="vs-section">
            <h4>Top {topVehicles.length} Vehicles (sorted by {sortBy.replace(/_/g, ' ')})</h4>
            <div style={{ overflowX: 'auto' }}>
              <table className="cs-table">
                <thead>
                  <tr>
                    <th>Vehicle</th>
                    <th>Routes</th>
                    <th>Total km</th>
                    <th>Total Cost</th>
                    <th>Total CO₂</th>
                    <th>Cost/km</th>
                    <th>CO₂/km</th>
                    <th>Last Seen</th>
                  </tr>
                </thead>
                <tbody>
                  {topVehicles.map(v => (
                    <tr key={v.vehicle_id}>
                      <td><strong>{v.vehicle_id}</strong></td>
                      <td>{v.n_routes}</td>
                      <td>{fmtNum(v.total_distance_km)}</td>
                      <td>{fmtCost(v.total_cost_sek)}</td>
                      <td>{fmtNum(v.total_co2_kg)}</td>
                      <td>{fmtPerKm(v.avg_cost_per_km_sek)}</td>
                      <td>{fmtPerKmKg(v.avg_co2_per_km_kg)}</td>
                      <td>
                        {v.last_sim_day !== null
                          ? `day ${v.last_sim_day} (${v.last_cycle_id})`
                          : v.last_cycle_id || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="cs-footnote">
            💡 Vehicles sorted by your chosen metric. Most-distance vehicle is highlighted
            in the bar chart. Efficiency columns (Cost/km, CO₂/km) reveal outliers —
            high values may indicate inefficient routing or unsuitable vehicle.
          </div>
        </>
      )}
    </div>
  )
}

export default VehicleStats
