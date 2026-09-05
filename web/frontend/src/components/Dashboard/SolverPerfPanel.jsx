/**
 * SolverPerfPanel - solver wall-time + match distance distribution (iter #53)
 *
 * 数据源:
 *   GET /api/persistence/cycle-duration-stats
 *   GET /api/persistence/match-distance-buckets
 *
 * 显示:
 * - 4 KPI 卡片: solver mean / median / p95 / slow cycles count
 * - Distance histogram bar chart (8 buckets)
 * - Distance stats: avg / median / p95
 *
 * 用途: 让用户看到:
 *       - Solver 性能是否稳定 (P95 vs mean gap)
 *       - 多少 cycle 跑得太慢 (>5s) 需要优化
 *       - 多数 match 是 short-haul 还是 long-haul
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

function _safeNum(v, decimals = 0) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(decimals)
}

function _msColor(ms) {
  if (ms === null || ms === undefined) return '#64748b'
  if (ms >= 5000) return '#dc2626'  // red, slow
  if (ms >= 1000) return '#f59e0b'  // orange
  if (ms >= 100) return '#22c55e'  // green
  return '#16a34a'  // bright green, fast
}

function _bucketColor(label) {
  // color-code by distance: short-haul green, long-haul orange/red
  const lte100 = ['0-5', '5-10', '10-25', '25-50', '50-100']
  const lte200 = ['100-200']
  const lte500 = ['200-500']
  if (lte100.includes(label)) return '#22c55e'
  if (lte200.includes(label)) return '#84cc16'
  if (lte500.includes(label)) return '#f59e0b'
  return '#dc2626'  // 500+
}

export function SolverPerfPanel() {
  const [durStats, setDurStats] = useState(null)
  const [distStats, setDistStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async () => {
    try {
      setLoading(true)
      const [durRes, distRes] = await Promise.all([
        fetch(`${API_BASE}/persistence/cycle-duration-stats`),
        fetch(`${API_BASE}/persistence/match-distance-buckets`),
      ])
      if (!durRes.ok) throw new Error(`duration: HTTP ${durRes.status}`)
      if (!distRes.ok) throw new Error(`distance: HTTP ${distRes.status}`)
      const durJson = await durRes.json()
      const distJson = await distRes.json()
      setDurStats(durJson)
      setDistStats(distJson)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  if (loading && !durStats && !distStats) {
    return <LoadingSpinner size="md" label="Loading solver perf + distance distribution…" />
  }

  if (error || (!durStats && !distStats)) {
    return (
      <div className="card solver-perf-panel">
        <h3>⚙️ Solver Perf & Distance (iter #53)</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No data yet. Run simulations to see solver perf metrics.'}
        </div>
      </div>
    )
  }

  const maxBucketCount = distStats?.buckets
    ? Math.max(1, ...distStats.buckets.map(b => b.count))
    : 1

  return (
    <div className="card solver-perf-panel">
      <h3>⚙️ Solver Perf & Distance Distribution</h3>

      <div className="kpi-grid">
        <div className="kpi-card" style={{ borderTop: `3px solid ${_msColor(durStats?.mean_ms)}` }}>
          <div className="kpi-label">Solver Mean</div>
          <div className="kpi-value">
            {_safeNum(durStats?.mean_ms, 1)}
            <span className="kpi-unit"> ms</span>
          </div>
          <div className="kpi-sub">
            {durStats?.n_cycles ?? 0} cycles · min {_safeNum(durStats?.min_ms, 0)} · max {_safeNum(durStats?.max_ms, 0)}
          </div>
        </div>

        <div className="kpi-card" style={{ borderTop: `3px solid ${_msColor(durStats?.median_ms)}` }}>
          <div className="kpi-label">Solver Median (p50)</div>
          <div className="kpi-value">
            {_safeNum(durStats?.median_ms, 1)}
            <span className="kpi-unit"> ms</span>
          </div>
          <div className="kpi-sub">
            p95: {_safeNum(durStats?.p95_ms, 1)} ms · p99: {_safeNum(durStats?.p99_ms, 1)} ms
          </div>
        </div>

        <div
          className="kpi-card"
          style={{
            borderTop: `3px solid ${(durStats?.slow_cycles_count ?? 0) > 0 ? '#dc2626' : '#22c55e'}`,
          }}
        >
          <div className="kpi-label">Slow Cycles (≥5s)</div>
          <div className="kpi-value">{durStats?.slow_cycles_count ?? 0}</div>
          <div className="kpi-sub">
            Fast (&lt;100ms): {durStats?.fast_cycles_count ?? 0}
          </div>
        </div>

        <div className="kpi-card" style={{ borderTop: '3px solid #3b82f6' }}>
          <div className="kpi-label">Total Solver Time</div>
          <div className="kpi-value">
            {_safeNum(durStats?.total_solver_time_seconds, 1)}
            <span className="kpi-unit"> s</span>
          </div>
          <div className="kpi-sub">
            avg = {durStats?.n_cycles && durStats.n_cycles > 0
              ? ((durStats.total_solver_time_seconds / durStats.n_cycles) * 1000).toFixed(1)
              : '—'}{' '}
            ms/cycle
          </div>
        </div>
      </div>

      <div className="chart-card" style={{ marginTop: '1rem' }}>
        <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.95rem' }}>
          Match Distance Distribution (n={distStats?.total_matches ?? 0})
        </h4>
        <div className="distance-summary">
          <span>
            avg <strong>{_safeNum(distStats?.avg_distance_km, 1)} km</strong>
          </span>
          <span>
            median <strong>{_safeNum(distStats?.median_distance_km, 1)} km</strong>
          </span>
          <span>
            p95 <strong>{_safeNum(distStats?.p95_distance_km, 1)} km</strong>
          </span>
          <span>
            total <strong>{_safeNum(distStats?.total_distance_km, 0)} km</strong>
          </span>
        </div>

        <div className="distance-bars">
          {distStats?.buckets?.map((b) => (
            <div key={b.label} className="distance-bar-row">
              <div className="distance-bar-label">{b.label} km</div>
              <div className="distance-bar-track">
                <div
                  className="distance-bar-fill"
                  style={{
                    width: `${(b.count / maxBucketCount) * 100}%`,
                    background: _bucketColor(b.label),
                  }}
                  title={`${b.count} matches (${(b.share * 100).toFixed(1)}%)`}
                />
              </div>
              <div className="distance-bar-count">
                {b.count}
                <span className="distance-bar-share">
                  {' '}
                  ({(b.share * 100).toFixed(0)}%)
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="card-footnote">
        Solver stats: wall-time in ms · Distance buckets: 8 ranges · iter #53 · auto-refresh 60s
      </div>
    </div>
  )
}