/**
 * PerturbationImpactByMaterial.jsx — iter #46
 *
 * Per-material breakdown of perturbation impact: which materials get
 * hit most by active perturbations, and what's the avg effective vs
 * base multiplier ratio?
 *
 * Data source: GET /api/persistence/perturbation-impact-by-material
 *
 * Renders:
 * - Sortable table (n_perturbed DESC by default)
 * - Bars: perturbation_rate_pct (proportional cell color)
 * - avg_ratio badge: <1 = suppression, 1 = neutral, >1 = boost
 * - Summary KPIs: n_materials, n_perturbed_total, overall rate
 * - Window filter (since/until sim_day)
 */
import { useState, useEffect, useMemo } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

function rateColor(rate) {
  if (rate == null) return '#1e293b'
  if (rate < 20) return '#10b981'
  if (rate < 50) return '#f59e0b'
  if (rate < 80) return '#f97316'
  return '#ef4444'
}

function ratioColor(ratio) {
  if (ratio == null) return '#94a3b8'
  if (ratio < 0.95) return '#3b82f6'   // suppression
  if (ratio > 1.05) return '#ef4444'   // boost
  return '#10b981'                      // neutral
}

function ratioLabel(ratio) {
  if (ratio == null) return '—'
  if (ratio < 0.95) return '↓ suppress'
  if (ratio > 1.05) return '↑ boost'
  return '= neutral'
}

export function PerturbationImpactByMaterial() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [sinceSimDay, setSinceSimDay] = useState('')
  const [untilSimDay, setUntilSimDay] = useState('')
  const [sortKey, setSortKey] = useState('n_perturbed')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const params = new URLSearchParams()
    if (sinceSimDay) params.set('since_sim_day', sinceSimDay)
    if (untilSimDay) params.set('until_sim_day', untilSimDay)
    const url = `${API_BASE}/persistence/perturbation-impact-by-material${params.toString() ? '?' + params.toString() : ''}`
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
  }, [sinceSimDay, untilSimDay])

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

  if (loading) return <LoadingSpinner label="loading per-material perturbation impact..." />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data) return <div className="empty">No data available.</div>

  const { by_material, summary, window } = data
  const hasWindow = window.since_sim_day != null || window.until_sim_day != null

  return (
    <div className="chart-card">
      <h3>💥 Perturbation Impact by Material <span className="iter-badge">iter #46</span></h3>
      <p className="chart-subtitle">
        Which materials are most affected by active perturbations?
        Shows n_perturbed, perturbation_rate_pct, and avg_ratio
        (effective_multiplier / base_multiplier).
      </p>

      <div className="pibm-controls">
        <label className="pibm-label">
          Since sim_day:
          <input
            type="number"
            className="pibm-input"
            placeholder="(start)"
            value={sinceSimDay}
            onChange={e => setSinceSimDay(e.target.value)}
            style={{ width: '100px' }}
          />
        </label>
        <label className="pibm-label">
          Until sim_day:
          <input
            type="number"
            className="pibm-input"
            placeholder="(end)"
            value={untilSimDay}
            onChange={e => setUntilSimDay(e.target.value)}
            style={{ width: '100px' }}
          />
        </label>
        {(sinceSimDay || untilSimDay) && (
          <button
            className="pibm-clear"
            onClick={() => { setSinceSimDay(''); setUntilSimDay('') }}
          >
            clear window
          </button>
        )}
      </div>

      <div className="pibm-kpi-row">
        <div className="cs-kpi">
          <div className="kpi-label">Materials</div>
          <div className="kpi-value">{summary.n_materials}</div>
          <div className="kpi-sub">with supply offers</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Perturbed</div>
          <div className="kpi-value" style={{ color: '#f59e0b' }}>
            {summary.n_perturbed_total}
          </div>
          <div className="kpi-sub">supply points hit</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Overall Rate</div>
          <div className="kpi-value" style={{ color: rateColor(summary.overall_perturbation_rate_pct) }}>
            {summary.overall_perturbation_rate_pct.toFixed(1)}%
          </div>
          <div className="kpi-sub">of {summary.n_supply_offers_total} offers</div>
        </div>
      </div>

      {by_material.length === 0 ? (
        <div className="empty">
          No perturbations in this window. Try expanding the sim_day range
          or running a few cycles with perturbations active.
        </div>
      ) : (
        <div className="pibm-table-wrapper">
          <table className="pibm-table">
            <thead>
              <tr>
                <th onClick={() => setSortKey('material_type')} className="pibm-sortable">
                  Material {sortKey === 'material_type' && '↓'}
                </th>
                <th onClick={() => setSortKey('n_perturbed')} className="pibm-sortable">
                  Perturbed {sortKey === 'n_perturbed' && '↓'}
                </th>
                <th onClick={() => setSortKey('n_total')} className="pibm-sortable">
                  Total {sortKey === 'n_total' && '↓'}
                </th>
                <th onClick={() => setSortKey('perturbation_rate_pct')} className="pibm-sortable">
                  Rate {sortKey === 'perturbation_rate_pct' && '↓'}
                </th>
                <th onClick={() => setSortKey('avg_ratio')} className="pibm-sortable">
                  Ratio {sortKey === 'avg_ratio' && '↓'}
                </th>
                <th>Direction</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => (
                <tr key={i}>
                  <td className="pibm-material"><code>{row.material_type}</code></td>
                  <td className="pibm-num">{row.n_perturbed}</td>
                  <td className="pibm-num">{row.n_total}</td>
                  <td>
                    <div className="pibm-bar-cell">
                      <div
                        className="pibm-bar"
                        style={{
                          width: `${Math.min(100, row.perturbation_rate_pct)}%`,
                          background: rateColor(row.perturbation_rate_pct),
                        }}
                      />
                      <span className="pibm-bar-label">{row.perturbation_rate_pct.toFixed(1)}%</span>
                    </div>
                  </td>
                  <td>
                    {row.avg_ratio != null ? (
                      <span style={{ color: ratioColor(row.avg_ratio), fontWeight: 600 }}>
                        {row.avg_ratio.toFixed(2)}×
                      </span>
                    ) : '—'}
                  </td>
                  <td>
                    <span style={{ color: ratioColor(row.avg_ratio) }}>
                      {ratioLabel(row.avg_ratio)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="pibm-footnote">
        {hasWindow ? (
          <>Window: sim_day [{window.since_sim_day ?? '—'} ... {window.until_sim_day ?? '—'}]. </>        ) : (
          <>All-time window. </>
        )}
        Sortable columns: click header to sort. Ratio = effective / base multiplier.
        Blue = suppression, red = boost, green = neutral.
      </div>
    </div>
  )
}
