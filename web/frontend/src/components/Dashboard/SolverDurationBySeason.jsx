/**
 * SolverDurationBySeason - solver wall-time breakdown by season (iter #69)
 *
 * 数据源:
 *   GET /api/persistence/solver-duration-by-season
 *
 * 显示:
 * - 4 KPI cards (winter / spring / summer / fall): p50 + n_cycles + status badge
 * - Slowest/Fastest season badge + Δ% comparison
 * - Bar chart of p50 across seasons (color-graded: 绿<500ms, 黄<5s, 橙<30s, 红>30s)
 * - Per-season table: n_cycles / p50 / p95 / mean / min-max / stddev / status counts
 * - Solver status breakdown (OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN) per season
 * - Sim_day window filter (since/until)
 *
 * 用途: 让用户看到:
 *       - Solver wall_duration_ms 在 4 个季节间的差异
 *       - Winter 道路条件是否拖慢 solver
 *       - INFEASIBLE counts 是否在某季节 spike (harder instances)
 *       - 哪个 season 最快 / 最慢 + Δ%
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const SEASON_META = {
  winter: { emoji: '❄️', color: '#60a5fa', months: 'Dec–Feb', description: 'Cold road conditions' },
  spring: { emoji: '🌱', color: '#22c55e', months: 'Mar–May', description: 'Spring thaw' },
  summer: { emoji: '☀️', color: '#facc15', months: 'Jun–Aug', description: 'Peak season' },
  fall:   { emoji: '🍂', color: '#fb923c', months: 'Sep–Nov', description: 'Pre-winter ramp' },
}

function p50Color(ms) {
  if (ms === null || ms === undefined) return '#94a3b8'
  if (ms < 500) return '#22c55e'
  if (ms < 5000) return '#facc15'
  if (ms < 30000) return '#fb923c'
  return '#ef4444'
}

function statusColor(status) {
  if (status === 'OPTIMAL') return '#22c55e'
  if (status === 'FEASIBLE') return '#facc15'
  if (status === 'INFEASIBLE') return '#ef4444'
  return '#94a3b8'
}

export function SolverDurationBySeason() {
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
      const res = await fetch(`${API_BASE}/persistence/solver-duration-by-season?${params}`)
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
    return <LoadingSpinner size="md" label="Loading solver duration by season…" />
  }

  if (error || !data) {
    return (
      <div className="card solver-duration-by-season-panel">
        <h3>🌍 Solver Duration by Season</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No cycle data yet.'}
        </div>
      </div>
    )
  }

  const seasons = data.seasons || []
  const nTotal = data.n_cycles_evaluated || 0
  const nWithData = data.n_seasons_with_data || 0
  const slowest = data.slowest_season
  const fastest = data.fastest_season
  const deltaPct = data.slowest_vs_fastest_pct
  const maxP50 = Math.max(...seasons.map((s) => s.p50_ms || 0), 1)
  const seasonsByName = Object.fromEntries(seasons.map((s) => [s.season, s]))

  return (
    <div className="card solver-duration-by-season-panel">
      <div className="card-header-row">
        <h3>🌍 Solver Duration by Season</h3>
        <div className="card-controls">
          {slowest && fastest && slowest !== fastest && (
            <span
              className="season-extreme-badge"
              style={{
                backgroundColor: SEASON_META[slowest]?.color || '#94a3b8',
              }}
              title={`Slowest: ${slowest} (${data.slowest_p50_ms}ms), Fastest: ${fastest} (${data.fastest_p50_ms}ms)`}
            >
              {SEASON_META[slowest]?.emoji} {slowest} is{' '}
              {deltaPct !== null ? `+${deltaPct.toFixed(0)}%` : '—'} slower than{' '}
              {SEASON_META[fastest]?.emoji} {fastest}
            </span>
          )}
          <span className="card-badge">
            {nTotal} cycles · {nWithData}/4 seasons
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

      {/* 4-season KPI row */}
      <div className="season-kpi-row">
        {['winter', 'spring', 'summer', 'fall'].map((seasonName) => {
          const s = seasonsByName[seasonName]
          const meta = SEASON_META[seasonName]
          if (!s) return null
          const p50 = s.p50_ms
          const nCycles = s.n_cycles || 0
          const status = s.solver_status_counts || {}
          return (
            <div
              key={seasonName}
              className="season-kpi"
              style={{ borderColor: meta.color }}
              title={`${seasonName}: ${meta.months} · ${meta.description}`}
            >
              <div className="season-kpi-header">
                <span className="season-kpi-emoji">{meta.emoji}</span>
                <span className="season-kpi-name">{seasonName}</span>
              </div>
              <div className="season-kpi-months">{meta.months}</div>
              <div
                className="season-kpi-p50"
                style={{ color: p50Color(p50) }}
              >
                {p50 !== null ? `${p50.toFixed(0)} ms` : '—'}
              </div>
              <div className="season-kpi-meta">
                {nCycles} {nCycles === 1 ? 'cycle' : 'cycles'}
              </div>
              {nCycles > 0 && (
                <div className="season-kpi-status">
                  {Object.entries(status).map(([k, v]) =>
                    v > 0 ? (
                      <span
                        key={k}
                        className="status-pill"
                        style={{ backgroundColor: statusColor(k) + '33', color: statusColor(k) }}
                        title={`${k}: ${v} cycles`}
                      >
                        {k.slice(0, 1)} {v}
                      </span>
                    ) : null
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Bar chart of p50 across seasons */}
      {nTotal === 0 ? (
        <div className="empty-state">No cycles in this time window.</div>
      ) : (
        <>
          <div className="season-chart">
            <div className="season-chart-label">
              p50 wall_duration_ms per season
            </div>
            <div className="season-bars">
              {seasons.map((s) => {
                const meta = SEASON_META[s.season]
                const heightPct = s.p50_ms ? (s.p50_ms / maxP50) * 100 : 0
                return (
                  <div
                    key={s.season}
                    className="season-bar-col"
                    title={`${s.season}: p50=${s.p50_ms}ms, p95=${s.p95_ms}ms, mean=${s.mean_ms}ms, n=${s.n_cycles} cycles`}
                  >
                    <div
                      className="season-bar"
                      style={{
                        height: `${heightPct}%`,
                        backgroundColor: p50Color(s.p50_ms),
                        borderColor: meta?.color || '#94a3b8',
                      }}
                    >
                      {s.p50_ms !== null && s.p50_ms > 0 && (
                        <span className="season-bar-value">
                          {s.p50_ms.toFixed(0)}
                        </span>
                      )}
                    </div>
                    <div className="season-bar-label">
                      <div className="bar-label-name">
                        {meta?.emoji} {s.season}
                      </div>
                      <div className="bar-label-meta">n={s.n_cycles}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Per-season table */}
          <div className="season-table-wrap">
            <table className="season-table">
              <thead>
                <tr>
                  <th>Season</th>
                  <th>Months</th>
                  <th>Cycles</th>
                  <th>p50</th>
                  <th>p95</th>
                  <th>Mean</th>
                  <th>Range</th>
                  <th>Status mix</th>
                </tr>
              </thead>
              <tbody>
                {seasons.map((s) => {
                  const meta = SEASON_META[s.season]
                  const status = s.solver_status_counts || {}
                  const maxStatus = Math.max(...Object.values(status), 1)
                  return (
                    <tr key={s.season}>
                      <td>
                        <span className="season-name-cell" style={{ color: meta?.color }}>
                          {meta?.emoji} {s.season}
                        </span>
                      </td>
                      <td className="small">{s.months.join('-')}</td>
                      <td>{s.n_cycles}</td>
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
                        <div className="status-bar-mini">
                          {Object.entries(status).map(([k, v]) =>
                            v > 0 ? (
                              <span
                                key={k}
                                className="status-bar-segment"
                                style={{
                                  width: `${(v / maxStatus) * 100}%`,
                                  backgroundColor: statusColor(k),
                                }}
                                title={`${k}: ${v} cycles`}
                              />
                            ) : null
                          )}
                        </div>
                        <div className="status-counts-mini">
                          {Object.entries(status)
                            .filter(([_, v]) => v > 0)
                            .map(([k, v]) => `${k.slice(0, 1)}=${v}`)
                            .join(' ') || '—'}
                        </div>
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
        Seasons: winter {SEASON_META.winter.months} · spring {SEASON_META.spring.months} · summer{' '}
        {SEASON_META.summer.months} · fall {SEASON_META.fall.months}
        {' · '}
        slowest = season with highest p50 (excluding empty)
        {' · '}
        iter #69 · auto-refresh 60s
      </div>
    </div>
  )
}
