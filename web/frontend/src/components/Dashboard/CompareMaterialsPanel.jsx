/**
 * CompareMaterialsPanel - side-by-side comparison of two materials (iter #64)
 *
 * 数据源:
 *   GET /api/persistence/compare-materials?material_a=...&material_b=...
 *
 * 显示:
 * - Two material selectors with quick-pick chips for common materials
 * - Optional sim_day window filter
 * - Side-by-side KPI table (15 metrics)
 * - 5-axis winner badges (color-coded A=blue / B=green)
 * - Absolute + pct change columns
 *
 * 用途: 让用户看到:
 *       - 两种 material 哪个更 green (low co2_per_ton)
 *       - 哪种 material 匹配率更高
 *       - 哪种 material 更受 demand 欢迎 (demand_fulfillment_pct)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

// Common materials to quick-pick
const COMMON_MATERIALS = [
  'concrete', 'metal_scrap', 'wood_waste', 'mixed_waste', 'asphalt', 'plastic',
]

const METRIC_GROUPS = [
  {
    title: '📊 Volume',
    fields: ['total_matched_tons', 'total_supply_tons', 'total_demand_tons'],
  },
  {
    title: '🔄 Activity',
    fields: ['n_matches', 'n_supply_offers', 'n_demand_requests', 'match_rate'],
  },
  {
    title: '⚖️ Efficiency',
    fields: ['avg_tons_per_match', 'avg_distance_km'],
  },
  {
    title: '🌱 Sustainability',
    fields: ['total_co2_kg', 'co2_per_ton', 'total_cost_sek', 'cost_per_ton'],
  },
  {
    title: '🛒 Demand context',
    fields: ['demand_fulfillment_pct', 'total_profit_sek'],
  },
]

function fmt(value, key) {
  if (value === null || value === undefined) return '—'
  if (key.startsWith('n_')) return String(value)
  if (key === 'match_rate' || key === 'demand_fulfillment_pct') return value.toFixed(2)
  if (key === 'avg_distance_km') return value.toFixed(2)
  if (key.includes('per_ton')) return value.toFixed(3)
  if (key.includes('tons') || key.includes('co2')) return value.toFixed(1)
  return value.toFixed(2)
}

export function CompareMaterialsPanel() {
  const [materialA, setMaterialA] = useState('concrete')
  const [materialB, setMaterialB] = useState('metal_scrap')
  const [sinceDay, setSinceDay] = useState('')
  const [untilDay, setUntilDay] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async (
    a = materialA, b = materialB, sd = sinceDay, ud = untilDay
  ) => {
    if (!a || !b) {
      setData(null)
      return
    }
    if (a === b) {
      setError('material_a and material_b must be different')
      return
    }
    try {
      const params = new URLSearchParams({ material_a: a, material_b: b })
      if (sd !== '') params.set('since_sim_day', String(sd))
      if (ud !== '') params.set('until_sim_day', String(ud))
      const res = await fetch(`${API_BASE}/persistence/compare-materials?${params}`)
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
      fetchData(materialA, materialB, sinceDay, untilDay)
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    fetchData(materialA, materialB, sinceDay, untilDay)
  }, [materialA, materialB, sinceDay, untilDay])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading material comparison…" />
  }

  if (error || !data) {
    return (
      <div className="card compare-materials-panel">
        <h3>⚖️ Compare Materials</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No data yet.'}
        </div>
      </div>
    )
  }

  const a = data.material_a || {}
  const bMat = data.material_b || {}
  const diffs = data.differences || {}
  const winner = data.winner || {}

  return (
    <div className="card compare-materials-panel">
      <div className="card-header-row">
        <h3>⚖️ Compare Materials</h3>
      </div>

      <div className="filter-row">
        <label>Material A:</label>
        <input
          type="text"
          className="filter-input"
          placeholder="e.g. concrete"
          value={materialA}
          onChange={(e) => setMaterialA(e.target.value)}
        />
        <div className="quick-picks">
          {COMMON_MATERIALS.map((m) => (
            <button
              key={m}
              className={`chip-pick ${materialA === m ? 'active' : ''}`}
              onClick={() => setMaterialA(m)}
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      <div className="filter-row">
        <label>Material B:</label>
        <input
          type="text"
          className="filter-input"
          placeholder="e.g. metal_scrap"
          value={materialB}
          onChange={(e) => setMaterialB(e.target.value)}
        />
        <div className="quick-picks">
          {COMMON_MATERIALS.map((m) => (
            <button
              key={m}
              className={`chip-pick ${materialB === m ? 'active' : ''}`}
              onClick={() => setMaterialB(m)}
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      <div className="filter-row">
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

      {/* Winner badges */}
      {Object.keys(winner).length > 0 && (
        <div className="winner-badges">
          {Object.entries(winner).map(([axis, w]) => {
            if (!w) return null
            const matColor = w.material === a.material_type ? 'badge-a' : 'badge-b'
            const matLabel = w.material === a.material_type ? 'A' : 'B'
            const axisLabel = axis.replace(/_/g, ' ')
            return (
              <span key={axis} className={`winner-badge ${matColor}`}>
                {matLabel} wins {axisLabel}
                {w.by_pct !== null && w.by_pct !== undefined ? ` (+${w.by_pct}%)` : ''}
              </span>
            )
          })}
        </div>
      )}

      {/* Side-by-side KPI table */}
      {METRIC_GROUPS.map((group) => (
        <div key={group.title} className="metric-group">
          <h4>{group.title}</h4>
          <table className="data-table compare-m-table">
            <thead>
              <tr>
                <th>Metric</th>
                <th className="numeric header-a">A: {a.material_type || '—'}</th>
                <th className="numeric header-b">B: {bMat.material_type || '—'}</th>
                <th className="numeric">Δ (B − A)</th>
                <th className="numeric">%</th>
              </tr>
            </thead>
            <tbody>
              {group.fields.map((field) => {
                const va = a[field]
                const vb = bMat[field]
                const abs = diffs.absolute?.[field]
                const pct = diffs.pct_change?.[field]
                return (
                  <tr key={field}>
                    <td className="metric-name">{field}</td>
                    <td className="numeric cell-a">{fmt(va, field)}</td>
                    <td className="numeric cell-b">{fmt(vb, field)}</td>
                    <td className="numeric diff-cell">
                      {abs !== null && abs !== undefined
                        ? (abs >= 0 ? '+' : '') + fmt(abs, field)
                        : '—'}
                    </td>
                    <td className="numeric pct-cell">
                      {pct !== null && pct !== undefined
                        ? (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
                        : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ))}

      <div className="card-footnote">
        Window: {data.filter?.since_sim_day ?? '∞'} →{' '}
        {data.filter?.until_sim_day ?? '∞'}
        {' · '}
        iter #64 · auto-refresh 60s
      </div>
    </div>
  )
}