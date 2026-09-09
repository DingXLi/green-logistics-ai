/**
 * CycleDurationHistogram - solver-duration histogram (iter #64)
 *
 * 数据源:
 *   GET /api/persistence/cycle-duration-histogram
 *
 * 显示:
 * - Bar chart of cycle wall_duration_ms distribution across 8 fixed buckets
 * - Sim_day window filter (since / until)
 * - Summary stats card (mean / median / min / max / stddev / slow / fast)
 * - Bar shows count, hover shows count + pct
 *
 * 用途: 让用户看到:
 *       - Solver performance distribution (大部分 < 1s 还是 30s+?)
 *       - Outlier slow runs (60s+ bucket)
 *       - Bimodal distribution 趋势 (some fast, some slow)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

// Bar colors: blue (fast) → green → yellow → orange → red (slow)
function bucketColor(label) {
  if (label === '<100ms') return '#22c55e'      // green
  if (label === '100-500ms') return '#84cc16'    // lime
  if (label === '0.5-1s') return '#a3e635'      // light green
  if (label === '1-5s') return '#facc15'        // yellow
  if (label === '5-10s') return '#fb923c'       // orange
  if (label === '10-30s') return '#f97316'      // dark orange
  if (label === '30-60s') return '#ef4444'      // red
  if (label === '60s+') return '#b91c1c'        // dark red
  return '#64748b'
}

export function CycleDurationHistogram() {
  const [sinceDay, setSinceDay] = useState('')
  const [untilDay, setUntilDay] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async (sd = sinceDay, ud = untilDay) => {
    try {
      const params = new URLSearchParams({})
      if (sd !== '') params.set('since_sim_day', String(sd))
      if (ud !== '') params.set('until_sim_day', String(ud))
      const res = await fetch(`${API_BASE}/persistence/cycle-duration-histogram?${params}`)
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
  }, [])

  useEffect(() => {
    fetchData(sinceDay, untilDay)
  }, [sinceDay, untilDay])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading cycle duration histogram…" />
  }

  if (error || !data) {
    return (
      <div className="card cycle-duration-histogram-panel">
        <h3>⏱️ Cycle Duration Histogram</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No cycle data yet.'}
        </div>
      </div>
    )
  }

  const buckets = data.buckets || []
  const stats = data.stats || {}
  const maxCount = Math.max(...buckets.map((b) => b.count), 1)
  const nCycles = data.n_cycles || 0

  return (
    <div className="card cycle-duration-histogram-panel">
      <div className="card-header-row">
        <h3>⏱️ Cycle Duration Histogram</h3>
        <div className="card-controls">
          <span className="card-badge">
            {nCycles} cycles evaluated · 8 ranges
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

      {/* Summary stats */}
      <div className="histogram-stats">
        <div className="hist-stat">
          <div className="hist-stat-label">Mean</div>
          <div className="hist-stat-value">
            {stats.mean_ms !== null ? `${stats.mean_ms.toFixed(0)} ms` : '—'}
          </div>
        </div>
        <div className="hist-stat">
          <div className="hist-stat-label">Median</div>
          <div className="hist-stat-value">
            {stats.median_ms !== null ? `${stats.median_ms.toFixed(0)} ms` : '—'}
          </div>
        </div>
        <div className="hist-stat">
          <div className="hist-stat-label">Min / Max</div>
          <div className="hist-stat-value small">
            {stats.min_ms !== null ? stats.min_ms.toFixed(0) : '—'}
            {' / '}
            {stats.max_ms !== null ? stats.max_ms.toFixed(0) : '—'}
          </div>
        </div>
        <div className="hist-stat">
          <div className="hist-stat-label">Stddev</div>
          <div className="hist-stat-value">
            {stats.stddev_ms !== null ? `${stats.stddev_ms.toFixed(0)} ms` : '—'}
          </div>
        </div>
        <div className="hist-stat">
          <div className="hist-stat-label">Fast (&lt;100ms)</div>
          <div className="hist-stat-value text-green">
            {stats.fast_count}
          </div>
        </div>
        <div className="hist-stat">
          <div className="hist-stat-label">Slow (&ge;5s)</div>
          <div className="hist-stat-value text-red">
            {stats.slow_count}
          </div>
        </div>
        <div className="hist-stat">
          <div className="hist-stat-label">Total time</div>
          <div className="hist-stat-value small">
            {stats.total_seconds !== undefined ? `${stats.total_seconds.toFixed(1)} s` : '—'}
          </div>
        </div>
      </div>

      {/* Bar chart */}
      {nCycles === 0 ? (
        <div className="empty-state">No cycles in this time window.</div>
      ) : (
        <div className="histogram-bars">
          {buckets.map((b) => {
            const heightPct = maxCount > 0 ? (b.count / maxCount) * 100 : 0
            return (
              <div key={b.label} className="histogram-bar-col">
                <div
                  className="histogram-bar"
                  style={{
                    height: `${heightPct}%`,
                    backgroundColor: bucketColor(b.label),
                  }}
                  title={`${b.label}: ${b.count} cycles (${b.pct.toFixed(1)}%)`}
                >
                  {b.count > 0 && (
                    <span className="histogram-bar-value">{b.count}</span>
                  )}
                </div>
                <div className="histogram-bar-label">
                  <div className="bar-label-name">{b.label}</div>
                  <div className="bar-label-pct">
                    {b.pct > 0 ? `${b.pct.toFixed(1)}%` : '0%'}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div className="card-footnote">
        Window: {data.since_sim_day ?? '∞'} → {data.until_sim_day ?? '∞'}
        {' · '}
        iter #64 · auto-refresh 60s
      </div>
    </div>
  )
}