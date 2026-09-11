/**
 * CycleDurationByProblemSize - solver performance breakdown by problem size (iter #67)
 *
 * 数据源:
 *   GET /api/persistence/cycle-duration-by-problem-size
 *
 * 显示:
 * - Per-bucket KPI cards (count + pct_of_total)
 * - Per-bucket stats: median duration, cost/ton, distance, n_matches
 * - Solver status breakdown (optimal / feasible / infeasible)
 * - Scaling signal badge (linear / superlinear / sublinear / unknown)
 * - Sim_day window filter (since / until)
 *
 * 用途: 让用户看到:
 *       - Solver scaling: large buckets 是否 disproportionate 慢?
 *       - Cost/ton efficiency: 大 cycle 更高效还是更浪费?
 *       - Infeasible solver runs: 集中在哪个 bucket?
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

// Bucket colors — green (small) → red (xlarge) to visually communicate size
function bucketColor(label) {
  if (label === 'small') return '#22c55e'   // green
  if (label === 'medium') return '#84cc16'  // lime
  if (label === 'large') return '#f97316'   // orange
  if (label === 'xlarge') return '#ef4444'  // red
  return '#64748b'
}

function bucketTonsLabel(b) {
  if (b.max_tons === null) return `≥${b.min_tons}t`
  return `${b.min_tons}-${b.max_tons}t`
}

function fmtMs(v) {
  if (v === null || v === undefined) return '—'
  if (v >= 1000) return `${(v / 1000).toFixed(2)} s`
  return `${v.toFixed(0)} ms`
}

function fmtSek(v) {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(2)} SEK`
}

function fmtSekTons(v) {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(2)} SEK/t`
}

function fmtKm(v) {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(1)} km`
}

function scalingBadgeColor(signal) {
  if (signal === 'linear') return '#22c55e'
  if (signal === 'sublinear') return '#10b981'
  if (signal === 'superlinear') return '#ef4444'
  return '#64748b'
}

export function CycleDurationByProblemSize() {
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
      const res = await fetch(
        `${API_BASE}/persistence/cycle-duration-by-problem-size?${params}`,
      )
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
    return (
      <LoadingSpinner
        size="md"
        label="Loading cycle duration by problem size…"
      />
    )
  }

  if (error || !data) {
    return (
      <div className="card cycle-duration-by-problem-size-panel">
        <h3>📊 Cycle Duration by Problem Size</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No cycle data yet.'}
        </div>
      </div>
    )
  }

  const buckets = data.buckets || []
  const nCycles = data.n_cycles || 0
  const scalingSignal = data.scaling_signal || 'unknown'

  return (
    <div className="card cycle-duration-by-problem-size-panel">
      <div className="card-header-row">
        <h3>📊 Cycle Duration by Problem Size</h3>
        <div className="card-controls">
          <span className="card-badge">
            {nCycles} cycles · {data.n_buckets} buckets
          </span>
          <span
            className="card-badge"
            style={{
              backgroundColor: scalingBadgeColor(scalingSignal),
              color: 'white',
            }}
            title="Solver scaling signal: linear/sublinear are good; superlinear means large cycles take disproportionately longer."
          >
            scaling: {scalingSignal}
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

      {nCycles === 0 ? (
        <div className="empty-state">No cycles in this time window.</div>
      ) : (
        <div className="problem-size-grid">
          {buckets.map((b) => {
            const color = bucketColor(b.label)
            const stat = b.duration_ms
            return (
              <div
                key={b.label}
                className="problem-size-bucket"
                style={{ borderTop: `4px solid ${color}` }}
              >
                <div className="bucket-header">
                  <div className="bucket-title">
                    <span
                      className="bucket-dot"
                      style={{ backgroundColor: color }}
                    />
                    <strong>{b.label}</strong>
                    <span className="bucket-tons">{bucketTonsLabel(b)}</span>
                  </div>
                  <div className="bucket-count">
                    {b.count} cycles · {b.pct_of_total.toFixed(1)}%
                  </div>
                </div>

                <div className="bucket-stats">
                  <div className="bucket-stat">
                    <span className="bs-label">Median duration</span>
                    <span className="bs-value">{fmtMs(stat.median)}</span>
                  </div>
                  <div className="bucket-stat">
                    <span className="bs-label">Mean duration</span>
                    <span className="bs-value">{fmtMs(stat.mean)}</span>
                  </div>
                  <div className="bucket-stat">
                    <span className="bs-label">Min / Max</span>
                    <span className="bs-value small">
                      {fmtMs(stat.min)} / {fmtMs(stat.max)}
                    </span>
                  </div>
                  <div className="bucket-stat">
                    <span className="bs-label">Stddev</span>
                    <span className="bs-value">{fmtMs(stat.stddev)}</span>
                  </div>
                  <div className="bucket-stat">
                    <span className="bs-label">Cost/ton (median)</span>
                    <span className="bs-value">
                      {fmtSekTons(b.cost_per_ton_sek.median)}
                    </span>
                  </div>
                  <div className="bucket-stat">
                    <span className="bs-label">Distance (mean)</span>
                    <span className="bs-value">
                      {fmtKm(b.distance_km.mean)}
                    </span>
                  </div>
                  <div className="bucket-stat">
                    <span className="bs-label">Matches (mean)</span>
                    <span className="bs-value">
                      {b.n_matches.mean !== null
                        ? b.n_matches.mean.toFixed(1)
                        : '—'}
                    </span>
                  </div>
                  <div className="bucket-stat">
                    <span className="bs-label">Dominant material</span>
                    <span className="bs-value small">
                      {b.dominant_material || '—'}
                    </span>
                  </div>
                </div>

                <div className="bucket-solver-status">
                  {b.solver_status_counts.OPTIMAL > 0 && (
                    <span className="status-chip status-optimal">
                      ✓ {b.solver_status_counts.OPTIMAL} optimal
                    </span>
                  )}
                  {b.solver_status_counts.FEASIBLE > 0 && (
                    <span className="status-chip status-feasible">
                      ≈ {b.solver_status_counts.FEASIBLE} feasible
                    </span>
                  )}
                  {b.solver_status_counts.INFEASIBLE > 0 && (
                    <span className="status-chip status-infeasible">
                      ✗ {b.solver_status_counts.INFEASIBLE} infeasible
                    </span>
                  )}
                  {b.solver_status_counts.UNKNOWN > 0 && (
                    <span className="status-chip status-unknown">
                      ? {b.solver_status_counts.UNKNOWN} unknown
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div className="card-footnote">
        Window: {data.since_sim_day ?? '∞'} → {data.until_sim_day ?? '∞'}
        {' · '}
        iter #67 · auto-refresh 60s
      </div>
    </div>
  )
}