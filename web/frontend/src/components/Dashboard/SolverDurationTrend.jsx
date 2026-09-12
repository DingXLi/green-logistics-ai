/**
 * SolverDurationTrend - solver wall-time p50/p95 trend over time (iter #68)
 *
 * 数据源:
 *   GET /api/persistence/solver-duration-trend
 *
 * 显示:
 * - 4 KPI cards: window_size / n_windows / first_window_p50 / last_window_p50
 * - Trend badge (improving / declining / stable / unknown) + delta %
 * - Confidence pill (0-1)
 * - Per-window table: window_index / cycles / p50 / p95 / mean / min-max
 * - Bar visualization of p50 across windows
 * - Window size selector (3/5/10/15)
 * - Sim_day window filter (since/until)
 *
 * 用途: 让用户看到:
 *       - Solver wall_duration_ms 如何随 cycle 演化
 *       - Performance regression detection (after model changes)
 *       - 何时 solver 开始变慢
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

function trendBadge(trend) {
  if (trend === 'improving') return { color: '#22c55e', emoji: '⚡', label: 'Improving' }
  if (trend === 'declining') return { color: '#ef4444', emoji: '🐢', label: 'Declining' }
  if (trend === 'stable') return { color: '#64748b', emoji: '➖', label: 'Stable' }
  return { color: '#94a3b8', emoji: '?', label: 'Unknown' }
}

function p50Color(ms) {
  if (ms === null || ms === undefined) return '#94a3b8'
  if (ms < 500) return '#22c55e'
  if (ms < 5000) return '#facc15'
  if (ms < 30000) return '#fb923c'
  return '#ef4444'
}

export function SolverDurationTrend() {
  const [windowSize, setWindowSize] = useState(5)
  const [sinceDay, setSinceDay] = useState('')
  const [untilDay, setUntilDay] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async (ws = windowSize, sd = sinceDay, ud = untilDay) => {
    try {
      const params = new URLSearchParams({ window_size: String(ws) })
      if (sd !== '') params.set('since_sim_day', String(sd))
      if (ud !== '') params.set('until_sim_day', String(ud))
      const res = await fetch(`${API_BASE}/persistence/solver-duration-trend?${params}`)
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
      fetchData(windowSize, sinceDay, untilDay)
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    fetchData(windowSize, sinceDay, untilDay)
  }, [windowSize, sinceDay, untilDay])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading solver duration trend…" />
  }

  if (error || !data) {
    return (
      <div className="card solver-duration-trend-panel">
        <h3>📈 Solver Duration Trend</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No cycle data yet.'}
        </div>
      </div>
    )
  }

  const windows = data.windows || []
  const nWindows = data.n_windows || 0
  const nCycles = data.n_cycles_evaluated || 0
  const trendInfo = trendBadge(data.trend)
  const trendDelta = data.trend_delta_pct
  const confidence = data.trend_confidence || 0
  const maxP50 = Math.max(...windows.map((w) => w.p50_ms || 0), 1)

  return (
    <div className="card solver-duration-trend-panel">
      <div className="card-header-row">
        <h3>📈 Solver Duration Trend</h3>
        <div className="card-controls">
          <span
            className="trend-badge"
            style={{ backgroundColor: trendInfo.color }}
            title={`Solver wall-time trend (p50 first-half vs second-half)`}
          >
            {trendInfo.emoji} {trendInfo.label}
          </span>
          <span className="card-badge">
            {nCycles} cycles · {nWindows} windows
          </span>
        </div>
      </div>

      <div className="filter-row">
        <label>Window size:</label>
        <select
          className="filter-input filter-input-narrow"
          value={windowSize}
          onChange={(e) => setWindowSize(Number(e.target.value))}
        >
          <option value={3}>3 cycles</option>
          <option value={5}>5 cycles</option>
          <option value={10}>10 cycles</option>
          <option value={15}>15 cycles</option>
        </select>

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

      {/* KPI strip */}
      <div className="trend-kpi-row">
        <div className="trend-kpi">
          <div className="trend-kpi-label">First window p50</div>
          <div className="trend-kpi-value" style={{ color: p50Color(data.first_window_p50_ms) }}>
            {data.first_window_p50_ms !== null ? `${data.first_window_p50_ms.toFixed(0)} ms` : '—'}
          </div>
        </div>
        <div className="trend-kpi">
          <div className="trend-kpi-label">Last window p50</div>
          <div className="trend-kpi-value" style={{ color: p50Color(data.last_window_p50_ms) }}>
            {data.last_window_p50_ms !== null ? `${data.last_window_p50_ms.toFixed(0)} ms` : '—'}
          </div>
        </div>
        <div className="trend-kpi">
          <div className="trend-kpi-label">Δ (p50 %)</div>
          <div className="trend-kpi-value">
            {trendDelta !== null ? `${trendDelta > 0 ? '+' : ''}${trendDelta.toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="trend-kpi">
          <div className="trend-kpi-label">Confidence</div>
          <div className="trend-kpi-value">
            <span className="confidence-pill">
              {(confidence * 100).toFixed(0)}%
            </span>
          </div>
        </div>
      </div>

      {/* Window chart */}
      {nCycles === 0 ? (
        <div className="empty-state">No cycles in this time window.</div>
      ) : nWindows === 0 ? (
        <div className="empty-state">Need ≥ 2 cycles for trend analysis.</div>
      ) : (
        <>
          {/* p50 bar visualization */}
          <div className="trend-chart">
            <div className="trend-chart-label">p50 wall_duration_ms per window</div>
            <div className="trend-bars">
              {windows.map((w) => {
                const heightPct = maxP50 > 0 ? (w.p50_ms / maxP50) * 100 : 0
                return (
                  <div key={w.window_index} className="trend-bar-col" title={`Window ${w.window_index}: p50=${w.p50_ms}ms, p95=${w.p95_ms}ms, n=${w.n_cycles} cycles (${w.start_cycle_id} → ${w.end_cycle_id})`}>
                    <div
                      className="trend-bar"
                      style={{
                        height: `${heightPct}%`,
                        backgroundColor: p50Color(w.p50_ms),
                      }}
                    >
                      {w.p50_ms > 0 && (
                        <span className="trend-bar-value">{w.p50_ms.toFixed(0)}</span>
                      )}
                    </div>
                    <div className="trend-bar-label">
                      <div className="bar-label-name">W{w.window_index}</div>
                      <div className="bar-label-meta">n={w.n_cycles}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Per-window table */}
          <div className="trend-table-wrap">
            <table className="trend-table">
              <thead>
                <tr>
                  <th>Window</th>
                  <th>Cycles</th>
                  <th>Sim day</th>
                  <th>p50</th>
                  <th>p95</th>
                  <th>Mean</th>
                  <th>Range</th>
                </tr>
              </thead>
              <tbody>
                {windows.map((w) => (
                  <tr key={w.window_index}>
                    <td>
                      <span className="window-index-badge">W{w.window_index}</span>
                    </td>
                    <td>{w.n_cycles}</td>
                    <td className="small">
                      {w.start_sim_day === w.end_sim_day
                        ? w.start_sim_day
                        : `${w.start_sim_day} → ${w.end_sim_day}`}
                    </td>
                    <td>
                      <span
                        className="duration-cell"
                        style={{ backgroundColor: p50Color(w.p50_ms) + '33' }}
                      >
                        {w.p50_ms.toFixed(0)} ms
                      </span>
                    </td>
                    <td>{w.p95_ms.toFixed(0)} ms</td>
                    <td>{w.mean_ms.toFixed(0)} ms</td>
                    <td className="small">
                      {w.min_ms.toFixed(0)} → {w.max_ms.toFixed(0)} ms
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div className="card-footnote">
        Window size: {data.window_size} · trend threshold: ±10% p50 delta
        {' · '}
        confidence: {confidence * 100 < 100 ? 'low' : 'high'}
        {' · '}
        iter #68 · auto-refresh 60s
      </div>
    </div>
  )
}
