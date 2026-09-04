/**
 * MaterialBalance.jsx — iter #49
 *
 * Material supply vs demand balance panel.
 * Shows which materials have oversupply (waste) or unmet demand (shortage).
 *
 * Data source: GET /api/persistence/material-supply-demand-balance
 *
 * Renders:
 * - Sortable table: material / supply / demand / matched / fulfillment%
 * - Stacked bar showing supply + matched + unmet
 * - Fulfillment color: green ≥80%, yellow ≥50%, orange ≥20%, red <20%
 * - Window filter (since/until sim_day)
 * - 60s auto-refresh
 */
import { useState, useEffect, useMemo } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_INTERVAL_MS = 60000

function fulfillmentColor(pct) {
  if (pct == null) return '#94a3b8'
  if (pct >= 80) return '#10b981'  // well-served
  if (pct >= 50) return '#facc15'  // moderate
  if (pct >= 20) return '#f59e0b'  // undersupplied
  return '#ef4444'                 // severe shortage
}

function excessColor(tons) {
  if (tons == null) return '#94a3b8'
  if (tons > 0) return '#f59e0b'   // oversupply
  if (tons < 0) return '#ef4444'   // negative = undersupply
  return '#10b981'                  // balanced
}

export function MaterialBalance() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [sortKey, setSortKey] = useState('unmet_demand_tons')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const params = new URLSearchParams()
    if (since) params.set('since_sim_day', since)
    if (until) params.set('until_sim_day', until)
    const url = `${API_BASE}/persistence/material-supply-demand-balance${params.toString() ? '?' + params.toString() : ''}`
    fetch(url)
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
  }, [since, until])

  // Auto-refresh every 60s
  useEffect(() => {
    const id = setInterval(() => {
      const params = new URLSearchParams()
      if (since) params.set('since_sim_day', since)
      if (until) params.set('until_sim_day', until)
      const url = `${API_BASE}/persistence/material-supply-demand-balance${params.toString() ? '?' + params.toString() : ''}`
      fetch(url)
        .then(r => r.json())
        .then(d => setData(d))
        .catch(e => setError(e.message))
    }, REFRESH_INTERVAL_MS)
    return () => clearInterval(id)
  }, [since, until])

  const sorted = useMemo(() => {
    if (!data?.by_material) return []
    const arr = [...data.by_material]
    arr.sort((a, b) => {
      const av = a[sortKey] ?? 0
      const bv = b[sortKey] ?? 0
      if (typeof av === 'string') return av.localeCompare(bv)
      return bv - av
    })
    return arr
  }, [data, sortKey])

  if (loading && !data) return <LoadingSpinner label="loading material balance..." />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data) return <div className="empty">No data available.</div>

  const { by_material } = data
  const totalSupply = by_material.reduce((s, r) => s + (r.total_supply_tons || 0), 0)
  const totalDemand = by_material.reduce((s, r) => s + (r.total_demand_tons || 0), 0)
  const totalMatched = by_material.reduce((s, r) => s + (r.total_matched_tons || 0), 0)
  const totalUnmet = by_material.reduce((s, r) => s + Math.max(0, r.unmet_demand_tons || 0), 0)
  const totalExcess = by_material.reduce((s, r) => s + Math.max(0, r.excess_supply_tons || 0), 0)
  const overallFulfillment = totalDemand > 0 ? (totalMatched / totalDemand * 100) : 0

  return (
    <div className="chart-card">
      <h3>⚖️ Material Supply-Demand Balance <span className="iter-badge">iter #49</span></h3>
      <p className="chart-subtitle">
        Which materials have oversupply (waste) or unmet demand (shortage)?
        Useful for tuning daily_capacity and demand_weights in WorldBuilder.
      </p>

      <div className="mb-controls">
        <label className="mb-label">
          Since sim_day:
          <input
            type="number"
            className="mb-input"
            placeholder="(start)"
            value={since}
            onChange={e => setSince(e.target.value)}
            style={{ width: '90px' }}
          />
        </label>
        <label className="mb-label">
          Until sim_day:
          <input
            type="number"
            className="mb-input"
            placeholder="(end)"
            value={until}
            onChange={e => setUntil(e.target.value)}
            style={{ width: '90px' }}
          />
        </label>
        {(since || until) && (
          <button
            className="mb-clear"
            onClick={() => { setSince(''); setUntil('') }}
          >
            clear window
          </button>
        )}
      </div>

      <div className="cs-kpi-row">
        <div className="cs-kpi">
          <div className="kpi-label">Total Supply</div>
          <div className="kpi-value" style={{ color: '#3b82f6' }}>
            {totalSupply.toFixed(0)}t
          </div>
          <div className="kpi-sub">across {by_material.length} materials</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Total Demand</div>
          <div className="kpi-value" style={{ color: '#f59e0b' }}>
            {totalDemand.toFixed(0)}t
          </div>
          <div className="kpi-sub">requested</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Matched</div>
          <div className="kpi-value" style={{ color: '#10b981' }}>
            {totalMatched.toFixed(0)}t
          </div>
          <div className="kpi-sub">actually delivered</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Fulfillment</div>
          <div className="kpi-value" style={{ color: fulfillmentColor(overallFulfillment) }}>
            {overallFulfillment.toFixed(1)}%
          </div>
          <div className="kpi-sub">{totalUnmet.toFixed(0)}t unmet</div>
        </div>
      </div>

      {by_material.length === 0 ? (
        <div className="empty">No material data in this window.</div>
      ) : (
        <div className="mb-table-wrapper">
          <table className="mb-table">
            <thead>
              <tr>
                <th onClick={() => setSortKey('material_type')} className="mb-sortable">
                  Material {sortKey === 'material_type' && '↓'}
                </th>
                <th onClick={() => setSortKey('total_supply_tons')} className="mb-sortable">
                  Supply (t) {sortKey === 'total_supply_tons' && '↓'}
                </th>
                <th onClick={() => setSortKey('total_demand_tons')} className="mb-sortable">
                  Demand (t) {sortKey === 'total_demand_tons' && '↓'}
                </th>
                <th onClick={() => setSortKey('total_matched_tons')} className="mb-sortable">
                  Matched (t) {sortKey === 'total_matched_tons' && '↓'}
                </th>
                <th onClick={() => setSortKey('demand_fulfillment_pct')} className="mb-sortable">
                  Fulfillment {sortKey === 'demand_fulfillment_pct' && '↓'}
                </th>
                <th onClick={() => setSortKey('excess_supply_tons')} className="mb-sortable">
                  Excess {sortKey === 'excess_supply_tons' && '↓'}
                </th>
                <th onClick={() => setSortKey('unmet_demand_tons')} className="mb-sortable">
                  Unmet {sortKey === 'unmet_demand_tons' && '↓'}
                </th>
                <th>Bar</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => {
                const sup = row.total_supply_tons || 0
                const dem = row.total_demand_tons || 0
                const mat = row.total_matched_tons || 0
                const fulfillment = row.demand_fulfillment_pct
                const max = Math.max(sup, dem, 1)
                return (
                  <tr key={i}>
                    <td><code>{row.material_type}</code></td>
                    <td className="mb-num">{sup.toFixed(1)}</td>
                    <td className="mb-num">{dem.toFixed(1)}</td>
                    <td className="mb-num">{mat.toFixed(1)}</td>
                    <td>
                      <span
                        className="mb-pill"
                        style={{ background: fulfillmentColor(fulfillment), color: '#fff' }}
                      >
                        {fulfillment != null ? `${fulfillment.toFixed(1)}%` : '—'}
                      </span>
                    </td>
                    <td className="mb-num" style={{ color: excessColor(row.excess_supply_tons) }}>
                      {row.excess_supply_tons > 0 ? `+${row.excess_supply_tons.toFixed(1)}` : row.excess_supply_tons?.toFixed(1) ?? '—'}
                    </td>
                    <td className="mb-num" style={{ color: excessColor(-(row.unmet_demand_tons || 0)) }}>
                      {row.unmet_demand_tons > 0 ? `-${row.unmet_demand_tons.toFixed(1)}` : row.unmet_demand_tons?.toFixed(1) ?? '—'}
                    </td>
                    <td className="mb-bar-cell">
                      <div className="mb-bar-wrapper">
                        <div
                          className="mb-bar-supply"
                          style={{ width: `${(sup / max) * 100}%` }}
                          title={`Supply: ${sup.toFixed(1)}t`}
                        />
                        <div
                          className="mb-bar-matched"
                          style={{ width: `${(mat / max) * 100}%` }}
                          title={`Matched: ${mat.toFixed(1)}t`}
                        />
                        <div
                          className="mb-bar-demand"
                          style={{ width: `${(dem / max) * 100}%` }}
                          title={`Demand: ${dem.toFixed(1)}t`}
                        />
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="mb-legend">
        <span className="mb-legend-item">
          <span className="mb-legend-swatch mb-bar-supply" /> Supply
        </span>
        <span className="mb-legend-item">
          <span className="mb-legend-swatch mb-bar-matched" /> Matched
        </span>
        <span className="mb-legend-item">
          <span className="mb-legend-swatch mb-bar-demand" /> Demand
        </span>
        <span className="mb-legend-foot">
          60s auto-refresh. Fulfillment = matched / demand. Excess = supply - matched. Unmet = demand - matched.
        </span>
      </div>
    </div>
  )
}
