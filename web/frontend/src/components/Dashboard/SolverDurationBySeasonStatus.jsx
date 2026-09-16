/**
 * SolverDurationBySeasonStatus - 3D heatmap (season × status) (iter #72)
 *
 * 数据源:
 *   GET /api/persistence/solver-duration-by-season-status
 *
 * 显示:
 * - 4×4 = 16-cell heatmap (winter/spring/summer/fall × OPTIMAL/FEASIBLE/INFEASIBLE/UNKNOWN)
 *   Each cell shows: p50_ms (color-graded), n_cycles, pct_of_total
 * - Slowest cell badge + Δ% vs fastest cell
 * - Per-season rollup bar (4 columns)
 * - Per-status rollup bar (4 columns)
 * - Top-5 slowest cells table (operational triage)
 * - Global stats footer (n_cycles / global p50)
 *
 * 用途: 让用户看到:
 *       - 3D view: which (season, status) pair is the slowest hotspot
 *       - Spot "winter INFEASIBLE" or "summer OPTIMAL" sweet spots
 *       - Marginal rollups: per-season across statuses / per-status across seasons
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const SEASONS = ['winter', 'spring', 'summer', 'fall']
const SEASON_META = {
  winter: { emoji: '❄️', name: 'Winter' },
  spring: { emoji: '🌱', name: 'Spring' },
  summer: { emoji: '☀️', name: 'Summer' },
  fall:   { emoji: '🍂', name: 'Fall' },
}
const STATUSES = ['OPTIMAL', 'FEASIBLE', 'INFEASIBLE', 'UNKNOWN']
const STATUS_META = {
  OPTIMAL:    { emoji: '✅', color: '#22c55e', description: 'Best solution found' },
  FEASIBLE:   { emoji: '🟡', color: '#facc15', description: 'Heuristic solution' },
  INFEASIBLE: { emoji: '🔴', color: '#ef4444', description: 'No solution found' },
  UNKNOWN:    { emoji: '⚪', color: '#94a3b8', description: 'No status recorded' },
}

function p50Color(ms) {
  if (ms === null || ms === undefined) return '#94a3b8'
  if (ms < 500) return '#22c55e'
  if (ms < 5000) return '#facc15'
  if (ms < 30000) return '#fb923c'
  return '#ef4444'
}

// Build a translucent fill color from p50 color hex
function p50Fill(ms) {
  if (ms === null || ms === undefined) return 'rgba(148, 163, 184, 0.1)'
  const c = p50Color(ms)
  return c + '22'  // 13% alpha
}

export function SolverDurationBySeasonStatus() {
  const [sinceDay, setSinceDay] = useState('')
  const [untilDay, setUntilDay] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async (sd = sinceDay, ud = untilDay) => {
    try {
      const params = new URLSearchParams()
      if (sd !== '') params.set('since_sim_day', String(sd))
      if (ud !== '') params.set('until_sim_day', String(ud))
      const res = await fetch(`${API_BASE}/persistence/solver-duration-by-season-status?${params}`)
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
      fetchData(sinceDay, untilDay)
    }, REFRESH_MS)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    fetchData(sinceDay, untilDay)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sinceDay, untilDay])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading solver duration by season × status…" />
  }

  if (error || !data) {
    return (
      <div className="card solver-duration-3d-panel">
        <h3>🌡️ Solver Duration 3D Heatmap (Season × Status)</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No cycle data yet.'}
        </div>
      </div>
    )
  }

  const cells = data.cells || []
  const nTotal = data.n_cycles_evaluated || 0
  const nWithData = data.n_cells_with_data || 0
  const slowest = data.slowest_cell
  const fastest = data.fastest_cell
  const deltaPct = data.slowest_vs_fastest_pct
  const seasonRollup = data.season_rollup || []
  const statusRollup = data.status_rollup || []
  const top5 = data.top_5_slowest_cells || []
  const globalStats = data.global_stats || {}

  // Build lookup: cells[season][status] = cell
  const cellsByKey = {}
  for (const c of cells) {
    if (!cellsByKey[c.season]) cellsByKey[c.season] = {}
    cellsByKey[c.season][c.status] = c
  }

  // Find max p50 across all cells for scale
  const maxP50 = Math.max(
    ...cells.map((c) => (c.p50_ms !== null ? c.p50_ms : 0)),
    1,
  )

  return (
    <div className="card solver-duration-3d-panel">
      <div className="card-header-row">
        <h3>🌡️ Solver Duration 3D Heatmap (Season × Status)</h3>
        <div className="card-controls">
          {slowest && fastest && (
            (slowest.season !== fastest.season || slowest.status !== fastest.status) ? (
              <span
                className="extreme-badge"
                style={{ backgroundColor: STATUS_META[slowest.status]?.color || '#94a3b8' }}
                title={`Slowest: ${slowest.season}/${slowest.status} (${slowest.p50_ms}ms), Fastest: ${fastest.season}/${fastest.status} (${fastest.p50_ms}ms)`}
              >
                {SEASON_META[slowest.season]?.emoji} {slowest.season}/{STATUS_META[slowest.status]?.emoji}{slowest.status} is{' '}
                {deltaPct !== null ? `+${deltaPct.toFixed(0)}%` : '—'} slower than{' '}
                {SEASON_META[fastest.season]?.emoji} {fastest.season}/{STATUS_META[fastest.status]?.emoji}{fastest.status}
              </span>
            ) : (
              <span className="extreme-badge" style={{ backgroundColor: '#22c55e' }}>
                All cells equal ({fastest.p50_ms}ms)
              </span>
            )
          )}
          <span className="card-badge">
            {nTotal} cycles · {nWithData}/16 cells
          </span>
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

      {/* 4×4 heatmap */}
      <div className="heatmap-wrap">
        <table className="heatmap-table">
          <thead>
            <tr>
              <th className="heatmap-corner"></th>
              {STATUSES.map((s) => {
                const meta = STATUS_META[s]
                return (
                  <th key={s} className="heatmap-status-header" style={{ borderBottomColor: meta.color }}>
                    <div className="heatmap-status-emoji">{meta.emoji}</div>
                    <div className="heatmap-status-name">{s}</div>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {SEASONS.map((season) => {
              const seasonMeta = SEASON_META[season]
              return (
                <tr key={season}>
                  <th className="heatmap-season-header">
                    <div className="heatmap-season-emoji">{seasonMeta.emoji}</div>
                    <div className="heatmap-season-name">{seasonMeta.name}</div>
                  </th>
                  {STATUSES.map((status) => {
                    const cell = cellsByKey[season]?.[status]
                    const statusMeta = STATUS_META[status]
                    if (!cell || cell.n_cycles === 0) {
                      return (
                        <td
                          key={status}
                          className="heatmap-cell heatmap-cell-empty"
                          title={`${season}/${status}: no data`}
                        >
                          <div className="heatmap-cell-empty-label">—</div>
                        </td>
                      )
                    }
                    const p50 = cell.p50_ms
                    const fillColor = p50Fill(p50)
                    const borderColor = p50Color(p50)
                    const heightPct = (p50 / maxP50) * 100
                    return (
                      <td
                        key={status}
                        className="heatmap-cell"
                        style={{
                          backgroundColor: fillColor,
                          borderColor: borderColor,
                        }}
                        title={`${season}/${status}: p50=${p50}ms, mean=${cell.mean_ms}ms, n=${cell.n_cycles} (${cell.pct_of_total.toFixed(1)}%)`}
                      >
                        <div className="heatmap-cell-fill" style={{ height: `${heightPct}%`, backgroundColor: borderColor }}></div>
                        <div className="heatmap-cell-content">
                          <div className="heatmap-cell-p50" style={{ color: borderColor }}>
                            {p50 !== null ? `${p50.toFixed(0)} ms` : '—'}
                          </div>
                          <div className="heatmap-cell-meta">
                            n={cell.n_cycles}
                          </div>
                          <div className="heatmap-cell-pct">
                            {cell.pct_of_total.toFixed(1)}%
                          </div>
                        </div>
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Marginal rollups */}
      <div className="rollup-row">
        <div className="rollup-block">
          <div className="rollup-block-title">Per-season rollup</div>
          <div className="rollup-bars">
            {seasonRollup.map((r) => {
              const meta = SEASON_META[r.season]
              const p50 = r.p50_ms
              const heightPct = p50 !== null && maxP50 > 0 ? (p50 / maxP50) * 100 : 0
              return (
                <div key={r.season} className="rollup-bar-col" title={`${r.season}: n=${r.n_cycles}, p50=${p50}ms`}>
                  <div className="rollup-bar-wrap">
                    <div
                      className="rollup-bar"
                      style={{
                        height: `${heightPct}%`,
                        backgroundColor: p50 !== null ? p50Color(p50) : '#94a3b8',
                      }}
                    >
                      {p50 !== null && <span className="rollup-bar-value">{p50.toFixed(0)}</span>}
                    </div>
                  </div>
                  <div className="rollup-bar-label">
                    <div className="rollup-label-emoji">{meta.emoji}</div>
                    <div className="rollup-label-name">{meta.name}</div>
                    <div className="rollup-label-n">n={r.n_cycles}</div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        <div className="rollup-block">
          <div className="rollup-block-title">Per-status rollup</div>
          <div className="rollup-bars">
            {statusRollup.map((r) => {
              const meta = STATUS_META[r.status]
              const p50 = r.p50_ms
              const heightPct = p50 !== null && maxP50 > 0 ? (p50 / maxP50) * 100 : 0
              return (
                <div key={r.status} className="rollup-bar-col" title={`${r.status}: n=${r.n_cycles}, p50=${p50}ms`}>
                  <div className="rollup-bar-wrap">
                    <div
                      className="rollup-bar"
                      style={{
                        height: `${heightPct}%`,
                        backgroundColor: p50 !== null ? p50Color(p50) : meta.color,
                      }}
                    >
                      {p50 !== null && <span className="rollup-bar-value">{p50.toFixed(0)}</span>}
                    </div>
                  </div>
                  <div className="rollup-bar-label">
                    <div className="rollup-label-emoji">{meta.emoji}</div>
                    <div className="rollup-label-name">{r.status}</div>
                    <div className="rollup-label-n">n={r.n_cycles}</div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Top-5 slowest cells */}
      {top5.length > 0 && (
        <div className="top5-wrap">
          <div className="top5-title">🔥 Top-5 slowest (season, status) cells</div>
          <table className="top5-table">
            <thead>
              <tr>
                <th>Season</th>
                <th>Status</th>
                <th>Cycles</th>
                <th>p50 ms</th>
                <th>Mean ms</th>
              </tr>
            </thead>
            <tbody>
              {top5.map((c) => {
                const seasonMeta = SEASON_META[c.season]
                const statusMeta = STATUS_META[c.status]
                return (
                  <tr key={`${c.season}/${c.status}`}>
                    <td>
                      <span className="top5-cell-tag" style={{ borderColor: '#94a3b8' }}>
                        {seasonMeta.emoji} {seasonMeta.name}
                      </span>
                    </td>
                    <td>
                      <span className="top5-cell-tag" style={{ borderColor: statusMeta.color, color: statusMeta.color }}>
                        {statusMeta.emoji} {c.status}
                      </span>
                    </td>
                    <td>{c.n_cycles}</td>
                    <td>
                      <span className="duration-cell" style={{ backgroundColor: p50Color(c.p50_ms) + '33' }}>
                        {c.p50_ms !== null ? `${c.p50_ms.toFixed(0)} ms` : '—'}
                      </span>
                    </td>
                    <td>{c.mean_ms !== null ? `${c.mean_ms.toFixed(0)} ms` : '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-footnote">
        {'Heatmap cell color: 🟢 <500ms · 🟡 <5s · 🟠 <30s · 🔴 ≥30s · ⚪ no data'}
        {' · '}
        Bar fill height = p50 / max(p50) across cells
        {' · '}
        iter #72 · auto-refresh 60s
      </div>
    </div>
  )
}
