/**
 * MaterialAggregates - 物料累计 KPI 表格 (iter #16)
 *
 * 数据源: GET /api/persistence/material-aggregates
 *
 * 显示:
 * - 每个 material_type 的累计 KPI
 * - total_available_tons / total_matched_tons / match_rate_pct
 * - n_distinct_supplies / n_matches / avg/max match_distance_km
 * - avg_quality_score
 * - 排序按 total_available DESC (后端已排序)
 *
 * 用途: 让用户看到整个 simulation 周期里:
 *       - 哪些材料最常被生成 (concrete / mixed_waste / paper / ...)
 *       - 哪些材料匹配率最高 (金属 100% vs 塑料 0%)
 *       - 哪些材料运输距离最长 (供需地理分布)
 */

import { useState, useEffect } from 'react'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const MATERIAL_ICONS = {
  concrete:        '🏗️',
  metal_scrap:     '🔩',
  wood_waste:      '🪵',
  mixed_waste:     '🗑️',
  plastic:         '🧴',
  paper_cardboard: '📦',
  wood:            '🪵',
  metal:           '🔩',
}

// match_rate_pct 颜色 (>70% 绿, 30-70 黄, <30 红)
function matchRateColor(rate) {
  if (rate == null) return '#9ca3af'
  if (rate >= 70) return '#22c55e'
  if (rate >= 30) return '#f59e0b'
  return '#ef4444'
}

export function MaterialAggregates() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filterMaterial, setFilterMaterial] = useState('')
  const [limit, setLimit] = useState(20)

  useEffect(() => {
    let cancelled = false
    const params = new URLSearchParams()
    if (filterMaterial) params.set('material_type', filterMaterial)
    params.set('limit', String(limit))
    const url = `${API_BASE}/persistence/material-aggregates?${params}`
    setLoading(true)
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
  }, [filterMaterial, limit])

  if (loading) return <LoadingSpinner label="Loading material aggregates…" />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data || data.length === 0) {
    return <div className="empty">No material aggregate data yet. Run a cycle first.</div>
  }

  // 计算 totals (for header strip)
  const totals = data.reduce((acc, m) => ({
    materials: acc.materials + 1,
    available: acc.available + (m.total_available_tons || 0),
    matched: acc.matched + (m.total_matched_tons || 0),
    matches: acc.matches + (m.n_matches || 0),
    supplies: acc.supplies + (m.n_distinct_supplies || 0),
  }), { materials: 0, available: 0, matched: 0, matches: 0, supplies: 0 })

  return (
    <div className="chart-card">
      <h3>📊 Material Aggregates</h3>
      <p className="chart-subtitle">
        Per-material-type cumulative KPIs across all simulation cycles.
        Sorted by total available tons.
      </p>

      {/* Header strip — totals */}
      <div className="materials-agg-totals">
        <div className="agg-stat">
          <div className="agg-stat-value">{totals.materials}</div>
          <div className="agg-stat-label">Materials</div>
        </div>
        <div className="agg-stat">
          <div className="agg-stat-value">{totals.supplies}</div>
          <div className="agg-stat-label">Supply points</div>
        </div>
        <div className="agg-stat">
          <div className="agg-stat-value">{totals.available.toFixed(1)}</div>
          <div className="agg-stat-label">Available (t)</div>
        </div>
        <div className="agg-stat">
          <div className="agg-stat-value">{totals.matched.toFixed(1)}</div>
          <div className="agg-stat-label">Matched (t)</div>
        </div>
        <div className="agg-stat">
          <div className="agg-stat-value">{totals.matches}</div>
          <div className="agg-stat-label">Matches</div>
        </div>
      </div>

      {/* Filters */}
      <div className="agg-filters">
        <input
          type="text"
          placeholder="filter by material…"
          value={filterMaterial}
          onChange={e => setFilterMaterial(e.target.value)}
          className="agg-filter-input"
        />
        <label className="agg-filter-label">
          Limit:
          <input
            type="number"
            min="1"
            max="200"
            value={limit}
            onChange={e => setLimit(parseInt(e.target.value) || 20)}
            className="agg-filter-input-small"
          />
        </label>
      </div>

      {/* Table */}
      <div className="agg-table-wrap">
        <table className="agg-table">
          <thead>
            <tr>
              <th>Material</th>
              <th>Supplies</th>
              <th>Cycles</th>
              <th>Available (t)</th>
              <th>Matched (t)</th>
              <th>Match Rate</th>
              <th>Avg Quality</th>
              <th>Matches</th>
              <th>Avg Dist (km)</th>
              <th>Max Dist (km)</th>
            </tr>
          </thead>
          <tbody>
            {data.map(m => {
              const icon = MATERIAL_ICONS[m.material_type] || '📦'
              const rateColor = matchRateColor(m.match_rate_pct)
              return (
                <tr key={m.material_type}>
                  <td>
                    <span className="agg-material-name">
                      <span className="agg-material-icon">{icon}</span>
                      {m.material_type.replace('_', ' ')}
                    </span>
                  </td>
                  <td>{m.n_distinct_supplies}</td>
                  <td>{m.n_cycles_with_material}</td>
                  <td className="num">{m.total_available_tons?.toFixed(1) ?? '—'}</td>
                  <td className="num">{m.total_matched_tons?.toFixed(1) ?? '—'}</td>
                  <td>
                    <span
                      className="agg-rate-badge"
                      style={{ backgroundColor: rateColor }}
                    >
                      {m.match_rate_pct?.toFixed(0) ?? 0}%
                    </span>
                  </td>
                  <td className="num">{m.avg_quality_score?.toFixed(1) ?? '—'}</td>
                  <td>{m.n_matches}</td>
                  <td className="num">{m.avg_match_distance_km?.toFixed(1) ?? '—'}</td>
                  <td className="num">{m.max_match_distance_km?.toFixed(1) ?? '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="agg-footnote">
        Showing {data.length} materials · sorted by total_available_tons DESC.
      </div>
    </div>
  )
}

export default MaterialAggregates