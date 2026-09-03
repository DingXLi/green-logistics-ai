/**
 * CohortRetentionCrosstab.jsx — iter #45
 *
 * Heatmap visualization of period × material retention matrix from
 * /api/persistence/cohort-retention-crosstab. Each cell colored
 * by retention_rate_pct (red→yellow→green).
 */
import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

function colorForRate(rate) {
  if (rate === null || rate === undefined) return '#1e293b'  // gray (no data)
  if (rate >= 80) return '#10b981'  // emerald (excellent)
  if (rate >= 60) return '#22c55e'  // green
  if (rate >= 40) return '#eab308'  // yellow
  if (rate >= 20) return '#f97316'  // orange
  return '#ef4444'  // red
}

function trendColor(trend) {
  if (trend === 'improving') return '#10b981'
  if (trend === 'declining') return '#ef4444'
  if (trend === 'stable') return '#22c55e'
  return '#94a3b8'  // unknown → gray
}

function trendIcon(trend) {
  if (trend === 'improving') return '↑'
  if (trend === 'declining') return '↓'
  if (trend === 'stable') return '→'
  return '?'
}

function trendLabel(trend) {
  if (trend === 'improving') return 'improving'
  if (trend === 'declining') return 'declining'
  if (trend === 'stable') return 'stable'
  return 'unknown'
}

export function CohortRetentionCrosstab() {
  const [nPeriods, setNPeriods] = useState(4)
  const [periodUnit, setPeriodUnit] = useState('quartile')
  const [materialFilter, setMaterialFilter] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = (np = nPeriods, pu = periodUnit, mf = materialFilter) => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams({
      n_periods: String(np),
      period_unit: pu,
    })
    if (mf) params.set('material_type', mf)
    fetch(`${API_BASE}/persistence/cohort-retention-crosstab?${params}`)
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
    fetchData()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleFilterChange = (np, pu, mf) => {
    setNPeriods(np)
    setPeriodUnit(pu)
    setMaterialFilter(mf)
    fetchData(np, pu, mf)
  }

  if (loading && !data) {
    return (
      <div className="crosstab-panel">
        <div className="ct-header">
          <h3>🔥 Retention Heatmap (iter #45)</h3>
        </div>
        <LoadingSpinner label="Loading retention crosstab..." />
      </div>
    )
  }

  if (error) {
    return (
      <div className="crosstab-panel">
        <div className="ct-header">
          <h3>🔥 Retention Heatmap (iter #45)</h3>
        </div>
        <div className="ct-error">Error: {error}</div>
      </div>
    )
  }

  const materials = data?.materials || []
  const matrix = data?.matrix || []
  const cellCounts = data?.cell_counts || []
  const periodLabels = data?.period_labels || []
  const trends = data?.trend_per_material || {}

  return (
    <div className="crosstab-panel">
      <div className="ct-header">
        <h3>🔥 Retention Heatmap <span className="iter-badge">iter #45</span></h3>
        <button className="refresh-btn" onClick={() => fetchData()} disabled={loading}>
          {loading ? 'Refreshing...' : '🔄 Refresh'}
        </button>
      </div>

      <div className="ct-controls">
        <label className="ct-label">
          <span>Periods:</span>
          <input
            type="number"
            min="1"
            max="10"
            value={nPeriods}
            onChange={e => handleFilterChange(parseInt(e.target.value, 10) || 1, periodUnit, materialFilter)}
            className="ct-input"
          />
        </label>
        <label className="ct-label">
          <span>Unit:</span>
          <select
            value={periodUnit}
            onChange={e => handleFilterChange(nPeriods, e.target.value, materialFilter)}
            className="ct-select"
          >
            <option value="quartile">quartile</option>
            <option value="day">day</option>
            <option value="week">week</option>
            <option value="month">month</option>
          </select>
        </label>
        <label className="ct-label">
          <span>Material filter:</span>
          <input
            type="text"
            value={materialFilter}
            placeholder="(all materials)"
            onChange={e => handleFilterChange(nPeriods, periodUnit, e.target.value)}
            className="ct-input"
          />
        </label>
      </div>

      {materials.length === 0 ? (
        <div className="ct-empty">
          No data yet. Run cycles to populate retention metrics.
        </div>
      ) : (
        <>
          <div className="ct-legend">
            <span>Retention: </span>
            <span className="ct-legend-cell" style={{ background: '#ef4444' }}>0-20%</span>
            <span className="ct-legend-cell" style={{ background: '#f97316' }}>20-40%</span>
            <span className="ct-legend-cell" style={{ background: '#eab308' }}>40-60%</span>
            <span className="ct-legend-cell" style={{ background: '#22c55e' }}>60-80%</span>
            <span className="ct-legend-cell" style={{ background: '#10b981' }}>80-100%</span>
            <span className="ct-legend-cell" style={{ background: '#1e293b' }}>no data</span>
          </div>

          <div style={{ overflowX: 'auto' }}>
            <table className="crosstab-table">
              <thead>
                <tr>
                  <th className="ct-corner">Material ↓ / Period →</th>
                  {periodLabels.map((p, i) => (
                    <th key={i} className="ct-period-header">
                      P{p.period_idx}
                      <br />
                      <small>(day {p.sim_day_min}-{p.sim_day_max})</small>
                    </th>
                  ))}
                  <th className="ct-trend-col">Trend</th>
                </tr>
              </thead>
              <tbody>
                {materials.map((mat, j) => (
                  <tr key={j}>
                    <td className="ct-material-label">{mat}</td>
                    {periodLabels.map((_, i) => {
                      const rate = matrix[i]?.[j]
                      const count = cellCounts[i]?.[j]
                      return (
                        <td
                          key={i}
                          className="ct-cell"
                          style={{ background: colorForRate(rate) }}
                          title={rate !== null && rate !== undefined
                            ? `${mat} retention: ${rate}% (n=${count})`
                            : `${mat}: no data`}
                        >
                          <div className="ct-cell-value">
                            {rate !== null && rate !== undefined ? `${rate}%` : '—'}
                          </div>
                          <div className="ct-cell-count">n={count}</div>
                        </td>
                      )
                    })}
                    <td
                      className="ct-trend-cell"
                      style={{ color: trendColor(trends[mat]) }}
                    >
                      <span className="ct-trend-icon">{trendIcon(trends[mat])}</span>
                      <span className="ct-trend-label">{trendLabel(trends[mat])}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="cs-footnote">
            💡 Each cell shows retention% for (period × material). Color intensity
            reflects retention quality. Trend column compares first vs last period
            (improving &gt; +5%, declining &lt; -5%, stable otherwise).
            "n=" shows sample size — low n means less reliable.
          </div>
        </>
      )}
    </div>
  )
}

export default CohortRetentionCrosstab
