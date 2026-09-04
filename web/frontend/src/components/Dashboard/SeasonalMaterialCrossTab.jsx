/**
 * SeasonalMaterialCrossTab.jsx — iter #46
 *
 * Cross-tab heatmap of seasonal_multiplier by (material × month).
 * Shows how each material's supply-side seasonal multiplier varies across
 * the year. Useful for spotting "concrete peaks in summer, metal_scrap is
 * stable year-round" patterns.
 *
 * Data source: GET /api/persistence/seasonal-timeseries-by-material
 *
 * Renders:
 * - Material filter pills (click to highlight a single material)
 * - Heatmap table: rows = materials, cols = months (Jan-Dec)
 * - Cell color: blue gradient (low) → red gradient (high) for multiplier
 * - Cell value: seasonal_multiplier (e.g. 1.3)
 * - Empty state: "No data yet — run a few cycles"
 */
import { useState, useEffect, useMemo } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

function multiplierColor(mult) {
  // Map [0.5, 1.5] → blue (low) → yellow (mid) → red (high)
  if (mult == null) return '#1e293b'
  if (mult <= 1.0) {
    // 0.5 → 1.0: blue gradient
    const t = Math.max(0, (mult - 0.5) / 0.5)
    const r = Math.round(30 + (59 - 30) * t)
    const g = Math.round(58 + (130 - 58) * t)
    const b = Math.round(138 + (246 - 138) * t)
    return `rgb(${r}, ${g}, ${b})`
  }
  // 1.0 → 1.5: yellow → red
  const t = Math.min(1, (mult - 1.0) / 0.5)
  const r = Math.round(234 + (239 - 234) * t)
  const g = Math.round(179 + (68 - 179) * t)
  const b = Math.round(8 + (68 - 8) * t)
  return `rgb(${r}, ${g}, ${b})`
}

function textColor(mult) {
  if (mult == null) return '#94a3b8'
  // Use white text for very dark or very light cells
  if (mult < 0.7) return '#fff'
  if (mult > 1.3) return '#fff'
  return '#0f172a'
}

export function SeasonalMaterialCrossTab() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [highlightMaterial, setHighlightMaterial] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch(`${API_BASE}/persistence/seasonal-timeseries-by-material`)
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
  }, [])

  // Build lookup: matrix[material][month] → row
  const lookup = useMemo(() => {
    if (!data?.matrix) return {}
    const m = {}
    for (const row of data.matrix) {
      if (!m[row.material_type]) m[row.material_type] = {}
      m[row.material_type][row.seasonal_month] = row
    }
    return m
  }, [data])

  if (loading) return <LoadingSpinner label="loading seasonal × material data..." />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data) return <div className="empty">No data available.</div>

  const { materials, month_labels, matrix } = data

  if (materials.length === 0) {
    return (
      <div className="chart-card">
        <h3>🌦️ Seasonal × Material Cross-Tab <span className="iter-badge">iter #46</span></h3>
        <p className="chart-subtitle">
          How each material's supply-side seasonal multiplier varies across the year.
        </p>
        <div className="empty">
          No seasonal data yet. Run a few simulation cycles to populate supply_offers
          with seasonal_multiplier + seasonal_month tags.
        </div>
      </div>
    )
  }

  // Aggregate per-material averages
  const materialSummary = materials.map(m => {
    const rows = matrix.filter(r => r.material_type === m)
    const totalTons = rows.reduce((s, r) => s + (r.total_tons || 0), 0)
    const avgMult = rows.length > 0
      ? rows.reduce((s, r) => s + (r.avg_seasonal_multiplier || 1), 0) / rows.length
      : 1
    return { material: m, n_cells: rows.length, total_tons: totalTons, avg_mult: avgMult }
  })

  return (
    <div className="chart-card">
      <h3>🌦️ Seasonal × Material Cross-Tab <span className="iter-badge">iter #46</span></h3>
      <p className="chart-subtitle">
        Cells show avg <code>seasonal_multiplier</code> for each (material, month)
        cell from supply_offers. Blue = below 1.0, yellow = 1.0, red = above 1.0.
        Click a material pill to highlight its row.
      </p>

      <div className="smc-pills">
        {materialSummary.map(m => (
          <button
            key={m.material}
            className={`smc-pill ${highlightMaterial === m.material ? 'active' : ''} ${highlightMaterial && highlightMaterial !== m.material ? 'dim' : ''}`}
            onClick={() => setHighlightMaterial(highlightMaterial === m.material ? null : m.material)}
          >
            <span className="smc-pill-name">{m.material}</span>
            <span className="smc-pill-meta">
              {m.total_tons.toFixed(1)}t · avg {m.avg_mult.toFixed(2)}
            </span>
          </button>
        ))}
      </div>

      <div className="smc-table-wrapper">
        <table className="smc-table">
          <thead>
            <tr>
              <th>Material</th>
              {month_labels.map((m, i) => (
                <th key={i} className="smc-month">{m}</th>
              ))}
              <th className="smc-summary">avg</th>
              <th className="smc-summary">tons</th>
            </tr>
          </thead>
          <tbody>
            {materials.map(mat => {
              const isHighlighted = !highlightMaterial || highlightMaterial === mat
              const isDim = highlightMaterial && highlightMaterial !== mat
              const rows = lookup[mat] || {}
              const cells = month_labels.map((_, idx) => {
                const month = idx + 1
                const row = rows[month]
                if (!row) {
                  return (
                    <td key={month} className="smc-cell smc-empty" title="no data">
                      —
                    </td>
                  )
                }
                return (
                  <td
                    key={month}
                    className="smc-cell"
                    style={{
                      backgroundColor: multiplierColor(row.avg_seasonal_multiplier),
                      color: textColor(row.avg_seasonal_multiplier),
                    }}
                    title={`${mat} · ${row.month_name} · ${row.avg_seasonal_multiplier.toFixed(2)}× · ${row.total_tons.toFixed(1)}t · n=${row.n_supply_offers}`}
                  >
                    {row.avg_seasonal_multiplier.toFixed(2)}
                  </td>
                )
              })
              // Find avg + tons from materialSummary
              const sum = materialSummary.find(s => s.material === mat)
              return (
                <tr key={mat} className={isDim ? 'smc-row-dim' : ''}>
                  <td className="smc-row-label">{mat}</td>
                  {cells}
                  <td className="smc-summary-cell">{sum.avg_mult.toFixed(2)}</td>
                  <td className="smc-summary-cell">{sum.total_tons.toFixed(1)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="smc-legend">
        <span className="smc-legend-label">multiplier:</span>
        <span className="smc-legend-cell" style={{ background: multiplierColor(0.5) }}>0.5</span>
        <span className="smc-legend-cell" style={{ background: multiplierColor(0.7) }}>0.7</span>
        <span className="smc-legend-cell" style={{ background: multiplierColor(1.0), color: '#0f172a' }}>1.0</span>
        <span className="smc-legend-cell" style={{ background: multiplierColor(1.2) }}>1.2</span>
        <span className="smc-legend-cell" style={{ background: multiplierColor(1.5) }}>1.5+</span>
        <span className="smc-legend-foot">
          blue = below baseline · yellow = neutral · red = above baseline
        </span>
      </div>
    </div>
  )
}
