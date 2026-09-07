/**
 * CycleComparisonPanel - compare two optimization cycles side-by-side (iter #59)
 *
 * 数据源:
 *   GET /api/persistence/cycle-history?limit=50  (populate dropdown options)
 *   GET /api/persistence/compare-cycles?cycle_id_a=...&cycle_id_b=...
 *
 * 显示:
 * - Two cycle pickers (A and B, default = first 2 cycles)
 * - Side-by-side KPI table: matches, tons, cost, CO2, distance, utilization
 * - Derived metrics: cost_per_ton, co2_per_ton, cost_per_km, co2_per_km, avg_tons_per_match
 * - Seasonal context: seasonal_factor_avg, perturbation_count
 * - Differences: absolute (b - a) and pct_change for each field
 * - Winner badges on 5 axes: lowest CO2/ton, lowest cost/ton, highest util,
 *   most matches, most tons
 *
 * 用途: 让用户能够:
 *       - 对比 greenest vs worst cycle
 *       - A/B test solver 改动
 *       - 比较前后 perturbation 效果
 *       - 季节性对比 (winter vs summer)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

// Winner axes metadata (for badge labels)
const WINNER_AXES = [
  { key: 'lowest_co2_per_ton_kg', label: '🌱 Lowest CO₂/ton', is_positive: 'lower' },
  { key: 'lowest_cost_per_ton_sek', label: '💰 Lowest cost/ton', is_positive: 'lower' },
  { key: 'highest_fleet_utilization_pct', label: '🚚 Highest utilization', is_positive: 'higher' },
  { key: 'most_matches', label: '🎯 Most matches', is_positive: 'higher' },
  { key: 'most_tons', label: '📦 Most tons', is_positive: 'higher' },
]

const FIELDS = [
  { key: 'n_matches', label: 'Matches', unit: '', higher_better: true },
  { key: 'total_tons', label: 'Total tons', unit: 't', higher_better: true },
  { key: 'total_cost_sek', label: 'Total cost', unit: 'SEK', higher_better: false },
  { key: 'total_co2_kg', label: 'Total CO₂', unit: 'kg', higher_better: false },
  { key: 'total_distance_km', label: 'Distance', unit: 'km', higher_better: false },
  { key: 'fleet_utilization_pct', label: 'Fleet util', unit: '%', higher_better: true },
  { key: 'cost_per_ton_sek', label: 'Cost/ton', unit: 'SEK/t', higher_better: false },
  { key: 'co2_per_ton_kg', label: 'CO₂/ton', unit: 'kg/t', higher_better: false },
  { key: 'cost_per_km_sek', label: 'Cost/km', unit: 'SEK/km', higher_better: false },
  { key: 'co2_per_km_kg', label: 'CO₂/km', unit: 'kg/km', higher_better: false },
  { key: 'avg_tons_per_match', label: 'Avg tons/match', unit: 't', higher_better: true },
]

export function CycleComparisonPanel() {
  const [cycles, setCycles] = useState([])
  const [cycleA, setCycleA] = useState('')
  const [cycleB, setCycleB] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Fetch cycle list to populate dropdowns
  useEffect(() => {
    const url = `${API_BASE}/persistence/cycle-history?limit=50`
    fetch(url)
      .then((r) => r.json())
      .then((d) => {
        const list = Array.isArray(d) ? d : []
        setCycles(list)
        // Default to first 2 cycles (newest first)
        if (list.length >= 2 && !cycleA && !cycleB) {
          setCycleA(list[0].cycle_id)
          setCycleB(list[1].cycle_id)
        } else if (list.length === 1 && !cycleA) {
          setCycleA(list[0].cycle_id)
        }
      })
      .catch((e) => setError(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Fetch comparison
  useEffect(() => {
    if (!cycleA || !cycleB || cycleA === cycleB) {
      setData(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    const url = `${API_BASE}/persistence/compare-cycles?cycle_id_a=${encodeURIComponent(cycleA)}&cycle_id_b=${encodeURIComponent(cycleB)}`
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((d) => {
        if (!cancelled) {
          setData(d)
          setLoading(false)
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e.message)
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [cycleA, cycleB])

  // Auto-refresh comparison
  useEffect(() => {
    if (!cycleA || !cycleB || cycleA === cycleB) return
    const id = setInterval(() => {
      const url = `${API_BASE}/persistence/compare-cycles?cycle_id_a=${encodeURIComponent(cycleA)}&cycle_id_b=${encodeURIComponent(cycleB)}`
      fetch(url)
        .then((r) => r.json())
        .then((d) => setData(d))
        .catch((e) => setError(e.message))
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [cycleA, cycleB])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading cycle comparison…" />
  }

  return (
    <div className="card cycle-comparison-panel">
      <div className="card-header-row">
        <h3>⚖️ Cycle Comparison</h3>
        <div className="card-controls">
          {data && (
            <span className="card-badge">
              {data.cycle_a?.cycle_id || '—'} vs {data.cycle_b?.cycle_id || '—'}
            </span>
          )}
        </div>
      </div>

      <div className="filter-row">
        <label>Cycle A:</label>
        <select
          className="filter-select"
          value={cycleA}
          onChange={(e) => setCycleA(e.target.value)}
        >
          <option value="">— pick cycle —</option>
          {cycles.map((c) => (
            <option key={c.cycle_id} value={c.cycle_id}>
              {c.cycle_id} (day {c.sim_day}, {c.n_matches} matches)
            </option>
          ))}
        </select>

        <label>Cycle B:</label>
        <select
          className="filter-select"
          value={cycleB}
          onChange={(e) => setCycleB(e.target.value)}
        >
          <option value="">— pick cycle —</option>
          {cycles.map((c) => (
            <option key={c.cycle_id} value={c.cycle_id}>
              {c.cycle_id} (day {c.sim_day}, {c.n_matches} matches)
            </option>
          ))}
        </select>

        {cycleA && cycleB && cycleA === cycleB && (
          <span className="error-inline">⚠️ Pick two different cycles</span>
        )}
      </div>

      {error && <div className="error-banner">⚠️ {error}</div>}

      {!data || !data.cycle_a || !data.cycle_b ? (
        <div className="empty-state">
          {cycleA && cycleB && cycleA !== cycleB
            ? 'No comparison data yet. Run a simulation to populate cycle history.'
            : 'Pick two cycles above to compare.'}
        </div>
      ) : (
        <>
          {/* Winner badges */}
          {data.winner && (
            <div className="winner-row">
              {WINNER_AXES.map((axis) => {
                const w = data.winner[axis.key]
                if (!w) return null
                const isA = w.cycle_id === data.cycle_a.cycle_id
                return (
                  <div
                    key={axis.key}
                    className={`winner-badge ${isA ? 'winner-a' : 'winner-b'}`}
                    title={`${w.a_value} vs ${w.b_value} (${w.direction})`}
                  >
                    <span className="winner-axis">{axis.label}:</span>
                    <strong>
                      {isA ? 'A' : 'B'} wins
                    </strong>
                    {w.by_abs != null && (
                      <span className="winner-magnitude">
                        ({w.by_pct != null
                          ? `${w.by_pct > 0 ? '+' : ''}${w.by_pct}%`
                          : `${w.by_abs}`})
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          {/* Side-by-side KPI table */}
          <div className="table-wrapper">
            <table className="data-table comparison-table">
              <thead>
                <tr>
                  <th>Field</th>
                  <th className="cycle-a-col">A: {data.cycle_a.cycle_id}</th>
                  <th className="cycle-b-col">B: {data.cycle_b.cycle_id}</th>
                  <th className="diff-col">Δ (B − A)</th>
                  <th className="diff-col">% change</th>
                </tr>
              </thead>
              <tbody>
                {FIELDS.map((f) => {
                  const va = data.cycle_a[f.key]
                  const vb = data.cycle_b[f.key]
                  const abs = data.differences.absolute[f.key]
                  const pct = data.differences.pct_change[f.key]
                  const fmt = (v) =>
                    v == null
                      ? '—'
                      : typeof v === 'number'
                        ? v.toFixed(2)
                        : v
                  return (
                    <tr key={f.key}>
                      <td>{f.label}</td>
                      <td className="numeric cycle-a-col">
                        {fmt(va)} <span className="metric-unit">{f.unit}</span>
                      </td>
                      <td className="numeric cycle-b-col">
                        {fmt(vb)} <span className="metric-unit">{f.unit}</span>
                      </td>
                      <td className="numeric diff-col">
                        {abs != null ? (
                          <>
                            {abs > 0 ? '+' : ''}
                            {abs.toFixed(2)}
                          </>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="numeric diff-col">
                        {pct != null ? (
                          <span
                            className={
                              pct > 0
                                ? f.higher_better
                                  ? 'pct-positive'
                                  : 'pct-negative'
                                : f.higher_better
                                  ? 'pct-negative'
                                  : 'pct-positive'
                            }
                          >
                            {pct > 0 ? '+' : ''}
                            {pct.toFixed(1)}%
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Seasonal / metadata row */}
          <div className="meta-row">
            <div className="meta-block">
              <span className="meta-label">Solver status</span>
              <span className="meta-a">{data.cycle_a.solver_status || '—'}</span>
              <span className="meta-vs">vs</span>
              <span className="meta-b">{data.cycle_b.solver_status || '—'}</span>
            </div>
            <div className="meta-block">
              <span className="meta-label">Wall duration</span>
              <span className="meta-a">{data.cycle_a.wall_duration_ms ?? '—'} ms</span>
              <span className="meta-vs">vs</span>
              <span className="meta-b">{data.cycle_b.wall_duration_ms ?? '—'} ms</span>
            </div>
            <div className="meta-block">
              <span className="meta-label">Seasonal factor (avg)</span>
              <span className="meta-a">
                {data.cycle_a.seasonal_factor_avg?.toFixed(3) ?? '—'}
              </span>
              <span className="meta-vs">vs</span>
              <span className="meta-b">
                {data.cycle_b.seasonal_factor_avg?.toFixed(3) ?? '—'}
              </span>
            </div>
            <div className="meta-block">
              <span className="meta-label">Perturbations</span>
              <span className="meta-a">{data.cycle_a.perturbation_count ?? 0}</span>
              <span className="meta-vs">vs</span>
              <span className="meta-b">{data.cycle_b.perturbation_count ?? 0}</span>
            </div>
          </div>
        </>
      )}

      <div className="card-footnote">
        iter #59 · auto-refresh 60s · Δ = B − A; green = improvement, red = regression
      </div>
    </div>
  )
}
