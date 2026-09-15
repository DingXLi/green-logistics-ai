/**
 * SolverDurationByStatus - solver wall-time breakdown by status (iter #71)
 *
 * 数据源:
 *   GET /api/persistence/solver-duration-by-status
 *
 * 显示:
 * - 4 status KPI cards (OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN):
 *   p50 + n_cycles + pct_of_total + status color
 * - Slowest/Fastest status badge + Δ% comparison
 * - 3 SLO-style rate pills: optimal_rate_pct / feasible_rate_pct / infeasible_rate_pct
 * - Bar chart of p50 across statuses (color-graded)
 * - Per-status table: n / pct / p50 / p95 / mean / min-max / stddev
 *                    / mean_cost_sek / mean_cost_per_ton_sek / mean_n_matches / mean_tons
 * - Sim_day window filter (since/until)
 *
 * 用途: 让用户看到:
 *       - Solver wall_duration_ms 在 4 种 solver outcome 间的差异
 *       - INFEASIBLE 是否比 OPTIMAL 慢很多 (search budget exhausted)
 *       - 各 status 的 cost/ton 差异 (FEASIBLE 可能更贵 — solver compromise)
 *       - infeasible_rate_pct 作为 SLO-style 健康指标
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

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

function rateColor(pct, kind) {
  // SLO-style color: high OPTIMAL good, high INFEASIBLE bad
  if (kind === 'OPTIMAL') {
    if (pct >= 80) return '#22c55e'
    if (pct >= 50) return '#facc15'
    return '#fb923c'
  }
  if (kind === 'INFEASIBLE') {
    if (pct >= 20) return '#ef4444'
    if (pct >= 5) return '#facc15'
    return '#22c55e'
  }
  // FEASIBLE / UNKNOWN neutral
  if (pct >= 50) return '#facc15'
  return '#94a3b8'
}

export function SolverDurationByStatus() {
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
      const res = await fetch(`${API_BASE}/persistence/solver-duration-by-status?${params}`)
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
    return <LoadingSpinner size="md" label="Loading solver duration by status…" />
  }

  if (error || !data) {
    return (
      <div className="card solver-duration-by-status-panel">
        <h3>📊 Solver Duration by Status</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No cycle data yet.'}
        </div>
      </div>
    )
  }

  const statuses = data.statuses || []
  const nTotal = data.n_cycles_evaluated || 0
  const nWithData = data.n_statuses_with_data || 0
  const slowest = data.slowest_status
  const fastest = data.fastest_status
  const deltaPct = data.slowest_vs_fastest_pct
  const maxP50 = Math.max(...statuses.map((s) => s.p50_ms || 0), 1)
  const statusesByName = Object.fromEntries(statuses.map((s) => [s.status, s]))

  return (
    <div className="card solver-duration-by-status-panel">
      <div className="card-header-row">
        <h3>📊 Solver Duration by Status</h3>
        <div className="card-controls">
          {slowest && fastest && slowest !== fastest && (
            <span
              className="status-extreme-badge"
              style={{
                backgroundColor: STATUS_META[slowest]?.color || '#94a3b8',
              }}
              title={`Slowest: ${slowest} (${data.slowest_p50_ms}ms), Fastest: ${fastest} (${data.fastest_p50_ms}ms)`}
            >
              {STATUS_META[slowest]?.emoji} {slowest} is{' '}
              {deltaPct !== null ? `+${deltaPct.toFixed(0)}%` : '—'} slower than{' '}
              {STATUS_META[fastest]?.emoji} {fastest}
            </span>
          )}
          <span className="card-badge">
            {nTotal} cycles · {nWithData}/4 statuses
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

      {/* SLO-style rate pills */}
      <div className="status-rate-row">
        <div
          className="status-rate-pill"
          style={{ borderColor: rateColor(data.optimal_rate_pct, 'OPTIMAL') }}
          title="OPTIMAL cycles / total cycles"
        >
          <div className="status-rate-label">✅ OPTIMAL</div>
          <div
            className="status-rate-value"
            style={{ color: rateColor(data.optimal_rate_pct, 'OPTIMAL') }}
          >
            {data.optimal_rate_pct.toFixed(1)}%
          </div>
        </div>
        <div
          className="status-rate-pill"
          style={{ borderColor: rateColor(data.feasible_rate_pct, 'FEASIBLE') }}
          title="FEASIBLE cycles / total cycles"
        >
          <div className="status-rate-label">🟡 FEASIBLE</div>
          <div className="status-rate-value" style={{ color: '#facc15' }}>
            {data.feasible_rate_pct.toFixed(1)}%
          </div>
        </div>
        <div
          className="status-rate-pill"
          style={{ borderColor: rateColor(data.infeasible_rate_pct, 'INFEASIBLE') }}
          title="INFEASIBLE cycles / total cycles (SLO: keep <5%)"
        >
          <div className="status-rate-label">🔴 INFEASIBLE</div>
          <div
            className="status-rate-value"
            style={{ color: rateColor(data.infeasible_rate_pct, 'INFEASIBLE') }}
          >
            {data.infeasible_rate_pct.toFixed(1)}%
          </div>
        </div>
      </div>

      {/* 4-status KPI row */}
      <div className="status-kpi-row">
        {['OPTIMAL', 'FEASIBLE', 'INFEASIBLE', 'UNKNOWN'].map((statusName) => {
          const s = statusesByName[statusName]
          const meta = STATUS_META[statusName]
          if (!s) return null
          const p50 = s.p50_ms
          const nCycles = s.n_cycles || 0
          const pct = s.pct_of_total || 0
          return (
            <div
              key={statusName}
              className="status-kpi"
              style={{ borderColor: meta.color }}
              title={`${statusName}: ${meta.description}`}
            >
              <div className="status-kpi-header">
                <span className="status-kpi-emoji">{meta.emoji}</span>
                <span className="status-kpi-name">{statusName}</span>
              </div>
              <div className="status-kpi-description">{meta.description}</div>
              <div
                className="status-kpi-p50"
                style={{ color: p50Color(p50) }}
              >
                {p50 !== null ? `${p50.toFixed(0)} ms` : '—'}
              </div>
              <div className="status-kpi-meta">
                {nCycles} {nCycles === 1 ? 'cycle' : 'cycles'} ({pct.toFixed(1)}%)
              </div>
            </div>
          )
        })}
      </div>

      {/* Bar chart of p50 across statuses */}
      {nTotal === 0 ? (
        <div className="empty-state">No cycles in this time window.</div>
      ) : (
        <>
          <div className="status-chart">
            <div className="status-chart-label">
              p50 wall_duration_ms per status
            </div>
            <div className="status-bars">
              {statuses.map((s) => {
                const meta = STATUS_META[s.status]
                const heightPct = s.p50_ms ? (s.p50_ms / maxP50) * 100 : 0
                return (
                  <div
                    key={s.status}
                    className="status-bar-col"
                    title={`${s.status}: p50=${s.p50_ms}ms, p95=${s.p95_ms}ms, mean=${s.mean_ms}ms, n=${s.n_cycles} cycles`}
                  >
                    <div
                      className="status-bar"
                      style={{
                        height: `${heightPct}%`,
                        backgroundColor: p50Color(s.p50_ms),
                        borderColor: meta?.color || '#94a3b8',
                      }}
                    >
                      {s.p50_ms !== null && s.p50_ms > 0 && (
                        <span className="status-bar-value">
                          {s.p50_ms.toFixed(0)}
                        </span>
                      )}
                    </div>
                    <div className="status-bar-label">
                      <div className="bar-label-name">
                        {meta?.emoji} {s.status}
                      </div>
                      <div className="bar-label-meta">n={s.n_cycles}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Per-status table */}
          <div className="status-table-wrap">
            <table className="status-table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Cycles</th>
                  <th>% total</th>
                  <th>p50</th>
                  <th>p95</th>
                  <th>Mean</th>
                  <th>Range</th>
                  <th>Cost/ton</th>
                  <th>n_matches</th>
                </tr>
              </thead>
              <tbody>
                {statuses.map((s) => {
                  const meta = STATUS_META[s.status]
                  return (
                    <tr key={s.status}>
                      <td>
                        <span className="status-name-cell" style={{ color: meta?.color }}>
                          {meta?.emoji} {s.status}
                        </span>
                      </td>
                      <td>{s.n_cycles}</td>
                      <td>{s.pct_of_total.toFixed(1)}%</td>
                      <td>
                        <span
                          className="duration-cell"
                          style={{ backgroundColor: p50Color(s.p50_ms) + '33' }}
                        >
                          {s.p50_ms !== null ? `${s.p50_ms.toFixed(0)} ms` : '—'}
                        </span>
                      </td>
                      <td>{s.p95_ms !== null ? `${s.p95_ms.toFixed(0)} ms` : '—'}</td>
                      <td>{s.mean_ms !== null ? `${s.mean_ms.toFixed(0)} ms` : '—'}</td>
                      <td className="small">
                        {s.min_ms !== null
                          ? `${s.min_ms.toFixed(0)} → ${s.max_ms.toFixed(0)} ms`
                          : '—'}
                      </td>
                      <td>
                        {s.mean_cost_per_ton_sek !== null
                          ? `${s.mean_cost_per_ton_sek.toFixed(0)} SEK/t`
                          : '—'}
                      </td>
                      <td>
                        {s.mean_n_matches !== null
                          ? s.mean_n_matches.toFixed(1)
                          : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div className="card-footnote">
        Solver outcomes: ✅ OPTIMAL (best found) · 🟡 FEASIBLE (heuristic) · 🔴 INFEASIBLE (no solution) · ⚪ UNKNOWN (no status recorded)
        {' · '}
        slowest = status with highest p50 (excluding empty)
        {' · '}
        iter #71 · auto-refresh 60s
      </div>
    </div>
  )
}
