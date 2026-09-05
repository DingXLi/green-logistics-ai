/**
 * DemandAggregates - per-demand_id 累计 KPI 表格 (iter #52)
 *
 * 数据源: GET /api/persistence/demand-aggregates
 *
 * 显示:
 * - 每个 demand_id 的累计 KPI
 * - total_required_tons / total_matched_tons / fulfillment_rate
 * - n_cycles_with_demand / n_matches / avg_required_tons
 * - 排序按 total_required_tons DESC (后端已排序)
 *
 * 用途: 让用户看到:
 *       - 哪些 demand site 需求量最大 (高需求区域)
 *       - 哪些 demand site 长期未被满足 (低 fulfillment)
 *       - 各 demand site 的需求波动 (n_cycles_with_demand × avg_required)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

function _fulfillmentColor(rate) {
  if (rate >= 0.8) return { color: '#16a34a', label: 'Met', emoji: '✅' }  // 绿
  if (rate >= 0.5) return { color: '#f59e0b', label: 'Partial', emoji: '⚠️' }  // 橙
  if (rate > 0) return { color: '#dc2626', label: 'Low', emoji: '🔴' }  // 红
  return { color: '#64748b', label: 'Unmet', emoji: '⚪' }  // 灰
}

export function DemandAggregates({ limit = 20, materialType = null, autoRefresh = true }) {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filterMat, setFilterMat] = useState(materialType || '')

  useEffect(() => {
    fetchData()
    if (!autoRefresh) return undefined
    const id = setInterval(fetchData, 60_000)  // 60s refresh
    return () => clearInterval(id)
  }, [filterMat, autoRefresh])

  const fetchData = async () => {
    try {
      setLoading(true)
      const params = new URLSearchParams({ limit: String(limit) })
      if (filterMat) params.set('material_type', filterMat)
      const res = await fetch(`${API_BASE}/persistence/demand-aggregates?${params}`)
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

  if (loading && data.length === 0) {
    return <LoadingSpinner size="md" label="Loading demand aggregates…" />
  }

  if (error) {
    return (
      <div className="card error-card">
        <h3>📦 Demand Aggregates (iter #52)</h3>
        <div className="error">Failed to fetch: {error}</div>
      </div>
    )
  }

  if (!data || data.length === 0) {
    return (
      <div className="card empty-card">
        <h3>📦 Demand Aggregates (iter #52)</h3>
        <div className="empty-state">
          No demand data yet. Run more simulations to populate.
          {filterMat && (
            <>
              {' '}
              <button onClick={() => setFilterMat('')} className="link-btn">
                Clear filter
              </button>
            </>
          )}
        </div>
      </div>
    )
  }

  // Compute summary stats
  const totalRequired = data.reduce((s, d) => s + (d.total_required_tons || 0), 0)
  const totalMatched = data.reduce((s, d) => s + (d.total_matched_tons || 0), 0)
  const overallFulfillment = totalRequired > 0 ? totalMatched / totalRequired : 0
  const unmetDemands = data.filter(d => (d.fulfillment_rate || 0) < 0.5).length

  return (
    <div className="card demand-aggregates-panel">
      <div className="card-header-row">
        <h3>📦 Demand Aggregates</h3>
        <div className="card-controls">
          <input
            type="text"
            placeholder="filter material…"
            value={filterMat}
            onChange={(e) => setFilterMat(e.target.value)}
            className="filter-input"
          />
        </div>
      </div>

      <div className="kpi-grid">
        <div className="kpi-card" style={{ borderTop: '3px solid #3b82f6' }}>
          <div className="kpi-label">Total Required</div>
          <div className="kpi-value">
            {Math.round(totalRequired).toLocaleString()}
            <span className="kpi-unit"> t</span>
          </div>
        </div>
        <div className="kpi-card" style={{ borderTop: '3px solid #22c55e' }}>
          <div className="kpi-label">Total Matched</div>
          <div className="kpi-value">
            {Math.round(totalMatched).toLocaleString()}
            <span className="kpi-unit"> t</span>
          </div>
        </div>
        <div
          className="kpi-card"
          style={{
            borderTop: `3px solid ${overallFulfillment >= 0.7 ? '#16a34a' : overallFulfillment >= 0.4 ? '#f59e0b' : '#dc2626'}`,
          }}
        >
          <div className="kpi-label">Fulfillment Rate</div>
          <div className="kpi-value">
            {(overallFulfillment * 100).toFixed(1)}
            <span className="kpi-unit">%</span>
          </div>
        </div>
        <div className="kpi-card" style={{ borderTop: '3px solid #f59e0b' }}>
          <div className="kpi-label">Unmet Demands (&lt;50%)</div>
          <div className="kpi-value">
            {unmetDemands}
            <span className="kpi-unit"> / {data.length}</span>
          </div>
        </div>
      </div>

      <div className="table-wrapper">
        <table className="data-table">
          <thead>
            <tr>
              <th>Demand ID</th>
              <th>Material</th>
              <th className="numeric">Required (t)</th>
              <th className="numeric">Matched (t)</th>
              <th>Fulfillment</th>
              <th className="numeric">Cycles</th>
              <th className="numeric">Matches</th>
              <th>Last Seen</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => {
              const fc = _fulfillmentColor(d.fulfillment_rate || 0)
              return (
                <tr key={d.demand_id}>
                  <td className="mono">{d.demand_id}</td>
                  <td>{d.material_type}</td>
                  <td className="numeric">{d.total_required_tons?.toFixed(1) ?? '—'}</td>
                  <td className="numeric">{d.total_matched_tons?.toFixed(1) ?? '—'}</td>
                  <td>
                    <span
                      className="fulfillment-badge"
                      style={{ color: fc.color }}
                      title={`Fulfillment: ${(d.fulfillment_rate * 100).toFixed(1)}%`}
                    >
                      {fc.emoji} {(d.fulfillment_rate * 100).toFixed(0)}%
                    </span>
                  </td>
                  <td className="numeric">{d.n_cycles_with_demand}</td>
                  <td className="numeric">{d.n_matches}</td>
                  <td className="mono">{d.last_sim_day ? `D${d.last_sim_day}` : '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="card-footnote">
        Showing top {data.length} demands by required tonnage · auto-refresh 60s · iter #52
      </div>
    </div>
  )
}