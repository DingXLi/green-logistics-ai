/**
 * TopPerformersPanel - top suppliers/vehicles by efficiency (iter #55)
 *
 * 数据源:
 *   GET /api/persistence/top-suppliers
 *   GET /api/persistence/top-vehicles
 *
 * 显示:
 * - 2 tabs (Suppliers / Vehicles)
 * - Metric selector (co2_per_ton / cost_per_ton / match_rate / etc.)
 * - Material filter for suppliers
 * - Top N table with rank, ID, value, context
 *
 * 用途: 让用户看到:
 *       - 哪些 supply agents 最 green (low co2/ton)
 *       - 哪些 vehicles 最高效 (low co2/km)
 *       - 哪些 materials 最 matched (high match_rate)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const SUPPLIER_METRICS = [
  { key: 'co2_per_ton', label: '🌱 CO₂ / ton', unit: 'kg/t', lower_better: true },
  { key: 'cost_per_ton', label: '💰 Cost / ton', unit: 'SEK/t', lower_better: true },
  { key: 'co2_per_match', label: '🌱 CO₂ / match', unit: 'kg/match', lower_better: true },
  { key: 'match_rate', label: '✅ Match rate', unit: 'matches/cycle', lower_better: false },
  { key: 'avg_distance', label: '📏 Avg distance', unit: 'km', lower_better: true },
]

const VEHICLE_METRICS = [
  { key: 'co2_per_ton_km', label: '🌱 CO₂ / (ton·km)', unit: 'kg/(t·km)', lower_better: true },
  { key: 'co2_per_km', label: '🌱 CO₂ / km', unit: 'kg/km', lower_better: true },
  { key: 'cost_per_km', label: '💰 Cost / km', unit: 'SEK/km', lower_better: true },
  { key: 'utilization', label: '⚡ Utilization', unit: '%', lower_better: false },
]

export function TopPerformersPanel() {
  const [tab, setTab] = useState('suppliers')
  const [supplierMetric, setSupplierMetric] = useState('co2_per_ton')
  const [vehicleMetric, setVehicleMetric] = useState('co2_per_km')
  const [materialFilter, setMaterialFilter] = useState('')
  const [suppliers, setSuppliers] = useState(null)
  const [vehicles, setVehicles] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchSuppliers = async (m = supplierMetric, mat = materialFilter) => {
    try {
      const params = new URLSearchParams({ metric: m, limit: '15' })
      if (mat) params.set('material_type', mat)
      const res = await fetch(`${API_BASE}/persistence/top-suppliers?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setSuppliers(json)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const fetchVehicles = async (m = vehicleMetric) => {
    try {
      const params = new URLSearchParams({ metric: m, limit: '15' })
      const res = await fetch(`${API_BASE}/persistence/top-vehicles?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setVehicles(json)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchSuppliers()
    fetchVehicles()
    const id = setInterval(() => {
      fetchSuppliers()
      fetchVehicles()
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (tab === 'suppliers') {
      fetchSuppliers(supplierMetric, materialFilter)
    } else {
      fetchVehicles(vehicleMetric)
    }
  }, [supplierMetric, vehicleMetric, materialFilter, tab])

  if (loading && !suppliers && !vehicles) {
    return <LoadingSpinner size="md" label="Loading top performers…" />
  }

  if (error || (!suppliers && !vehicles)) {
    return (
      <div className="card top-performers-panel">
        <h3>🏆 Top Performers (iter #55)</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No data yet. Run simulations to see top performers.'}
        </div>
      </div>
    )
  }

  const renderSuppliers = () => {
    if (!suppliers || !suppliers.top_suppliers || suppliers.top_suppliers.length === 0) {
      return <div className="empty-state">No supplier data for this filter.</div>
    }
    const metricDef = SUPPLIER_METRICS.find((m) => m.key === supplierMetric)
    return (
      <div className="table-wrapper">
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Supply ID</th>
              <th>Material</th>
              <th>{metricDef?.label || supplierMetric}</th>
              <th className="numeric">Matches</th>
              <th className="numeric">Matched (t)</th>
              <th className="numeric">Avg Dist (km)</th>
            </tr>
          </thead>
          <tbody>
            {suppliers.top_suppliers.map((s, idx) => (
              <tr key={s.supply_id}>
                <td className="rank-cell">{idx + 1}</td>
                <td className="mono">{s.supply_id}</td>
                <td>{s.material_type}</td>
                <td className="numeric metric-value">
                  {s.value !== null && s.value !== undefined ? s.value.toFixed(2) : '—'}{' '}
                  <span className="metric-unit">{metricDef?.unit || ''}</span>
                </td>
                <td className="numeric">{s.n_matches}</td>
                <td className="numeric">{s.total_matched_tons?.toFixed(1)}</td>
                <td className="numeric">{s.avg_distance_km?.toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  const renderVehicles = () => {
    if (!vehicles || !vehicles.top_vehicles || vehicles.top_vehicles.length === 0) {
      return <div className="empty-state">No vehicle data for this metric.</div>
    }
    const metricDef = VEHICLE_METRICS.find((m) => m.key === vehicleMetric)
    return (
      <div className="table-wrapper">
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Vehicle ID</th>
              <th>{metricDef?.label || vehicleMetric}</th>
              <th className="numeric">Routes</th>
              <th className="numeric">Distance (km)</th>
              <th className="numeric">CO₂ (kg)</th>
              <th className="numeric">Cost (SEK)</th>
            </tr>
          </thead>
          <tbody>
            {vehicles.top_vehicles.map((v, idx) => (
              <tr key={v.vehicle_id}>
                <td className="rank-cell">{idx + 1}</td>
                <td className="mono">{v.vehicle_id}</td>
                <td className="numeric metric-value">
                  {v.value !== null && v.value !== undefined ? v.value.toFixed(3) : '—'}{' '}
                  <span className="metric-unit">{metricDef?.unit || ''}</span>
                </td>
                <td className="numeric">{v.n_routes}</td>
                <td className="numeric">{v.total_distance_km?.toFixed(0)}</td>
                <td className="numeric">{v.total_co2_kg?.toFixed(1)}</td>
                <td className="numeric">{v.total_cost_sek?.toFixed(0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <div className="card top-performers-panel">
      <div className="card-header-row">
        <h3>🏆 Top Performers</h3>
        <div className="card-controls">
          <div className="tab-buttons">
            <button
              className={`tab-btn ${tab === 'suppliers' ? 'active' : ''}`}
              onClick={() => setTab('suppliers')}
            >
              🏭 Suppliers
            </button>
            <button
              className={`tab-btn ${tab === 'vehicles' ? 'active' : ''}`}
              onClick={() => setTab('vehicles')}
            >
              🚚 Vehicles
            </button>
          </div>
        </div>
      </div>

      {tab === 'suppliers' && (
        <>
          <div className="filter-row">
            <label>Metric:</label>
            <select
              className="filter-select"
              value={supplierMetric}
              onChange={(e) => setSupplierMetric(e.target.value)}
            >
              {SUPPLIER_METRICS.map((m) => (
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
          </div>
          {renderSuppliers()}
        </>
      )}

      {tab === 'vehicles' && (
        <>
          <div className="filter-row">
            <label>Metric:</label>
            <select
              className="filter-select"
              value={vehicleMetric}
              onChange={(e) => setVehicleMetric(e.target.value)}
            >
              {VEHICLE_METRICS.map((m) => (
                <option key={m.key} value={m.key}>{m.label}</option>
              ))}
            </select>
          </div>
          {renderVehicles()}
        </>
      )}

      <div className="card-footnote">
        Lower is better for CO₂/cost/distance · Higher is better for match_rate/utilization · 
        iter #55 · auto-refresh 60s
      </div>
    </div>
  )
}