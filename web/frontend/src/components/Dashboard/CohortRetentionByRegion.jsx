/**
 * CohortRetentionByRegion — supply retention broken down by GEOGRAPHIC REGION (iter #73)
 *
 * 数据源:
 *   GET /api/persistence/cohort-retention-by-region
 *
 * 显示:
 * - 4 region cards (Göteborg ⚓ / Borås 🧵 / Stockholm 🏛️ / unknown ❓)
 *   卡片: n_supply_ids / n_repeating / retention_rate_pct / one_time_pct
 * - Best/worst region badge + Δ% (e.g. "Borås is +50% better than Stockholm")
 * - Per-region retention bar chart
 * - Summary table: region / n_supply_ids / offers / retention / one-time %
 * - material_type filter
 * - 60s auto-refresh
 *
 * 用途: 让 ops 看 retention 模式是否随地理区域波动 (港口城市 vs 工业腹地)
 *
 * 跟 iter #70 CohortRetentionBySeason 互补 — season 切时间, region 切空间。
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

// Region colors — Sweden west→east gradient
const REGION_COLORS = {
  'Göteborg': '#0ea5e9',  // cyan-blue (port)
  'Borås':    '#a855f7',  // purple (textile)
  'Stockholm':'#f43f5e',  // rose (capital)
  'unknown':  '#64748b',  // slate
}

// Retention color grading: 0% red, 100% green
function retentionColor(pct) {
  if (pct === null || pct === undefined) return '#94a3b8'
  if (pct >= 80) return '#22c55e'  // green
  if (pct >= 50) return '#84cc16'  // lime
  if (pct >= 25) return '#f59e0b'  // amber
  return '#ef4444'                 // red
}

function fmtPct(v) {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(1)}%`
}

export function CohortRetentionByRegion() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [materialType, setMaterialType] = useState('')

  useEffect(() => {
    let cancelled = false
    const fetchData = async () => {
      try {
        const params = new URLSearchParams()
        if (materialType.trim()) {
          params.append('material_type', materialType.trim())
        }
        const url = `${API_BASE}/persistence/cohort-retention-by-region${
          params.toString() ? '?' + params.toString() : ''
        }`
        const resp = await fetch(url)
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const json = await resp.json()
        if (!cancelled) {
          setData(json)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) {
          setError(e.message || 'fetch failed')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    fetchData()
    const id = setInterval(fetchData, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [materialType])

  if (loading && !data) {
    return (
      <div className="card cohort-retention-by-region">
        <LoadingSpinner label="Loading cohort retention by region…" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="card cohort-retention-by-region">
        <div className="error-banner">⚠ {error}</div>
      </div>
    )
  }

  if (!data) return null

  const hasData = data.n_regions_with_data > 0

  return (
    <div className="card cohort-retention-by-region">
      <div className="card-header">
        <h3>🗺️ Cohort Retention by Region <span className="card-subtitle">(iter #73)</span></h3>
        <div className="card-controls">
          <input
            type="text"
            placeholder="Material type (e.g. concrete)"
            value={materialType}
            onChange={(e) => setMaterialType(e.target.value)}
            className="material-input"
          />
        </div>
      </div>

      {/* Best/Worst region badge */}
      {hasData && data.best_region && data.best_region !== data.worst_region && (
        <div className="region-extreme-badge">
          {regionMeta(data.best_region)?.emoji} <strong>{data.best_region}</strong> retention
          ({fmtPct(data.best_region_pct)}) is better than{' '}
          {regionMeta(data.worst_region)?.emoji} <strong>{data.worst_region}</strong>{' '}
          ({fmtPct(data.worst_region_pct)}) — Δ {fmtPct(data.worst_vs_best_pct)}
        </div>
      )}
      {hasData && data.best_region === data.worst_region && (
        <div className="region-extreme-badge tied">
          All regions with data tied at {fmtPct(data.best_region_pct)} retention
        </div>
      )}

      {!hasData && (
        <div className="empty-state">
          No cohort retention data for selected material filter. Try removing the
          filter to see all supplies.
        </div>
      )}

      {/* Region KPI grid */}
      <div className="region-kpi-row">
        {data.regions.map((r) => {
          const isBest = r.region === data.best_region
          const isWorst = r.region === data.worst_region && data.best_region !== data.worst_region
          const borderColor = REGION_COLORS[r.region] || REGION_COLORS.unknown
          const isRealRegion = r.region !== 'unknown'
          return (
            <div
              key={r.region}
              className={`region-card ${isBest ? 'best' : ''} ${isWorst ? 'worst' : ''} ${r.n_supply_ids === 0 ? 'empty' : ''} ${!isRealRegion ? 'unknown-bucket' : ''}`}
              style={{ borderColor }}
            >
              <div className="region-card-header">
                <span className="region-emoji-large">{r.region_emoji}</span>
                <div className="region-card-title">
                  <div className="region-name">{r.region_name}</div>
                  <div className="region-tagline">
                    {isRealRegion ? 'Sweden city' : 'NULL lat/lon'}
                  </div>
                </div>
                {isBest && <span className="region-tag best-tag">Best</span>}
                {isWorst && <span className="region-tag worst-tag">Worst</span>}
              </div>

              <div className="region-card-retention">
                <span
                  className="retention-value"
                  style={{ color: retentionColor(r.retention_rate_pct) }}
                >
                  {fmtPct(r.retention_rate_pct)}
                </span>
                <span className="retention-label">retention</span>
              </div>

              <div className="region-card-bar">
                <div
                  className="region-card-bar-fill"
                  style={{
                    width: `${r.retention_rate_pct}%`,
                    backgroundColor: retentionColor(r.retention_rate_pct),
                  }}
                />
              </div>

              <div className="region-card-stats">
                <div className="rc-stat">
                  <span className="rc-stat-label">Supplies</span>
                  <span className="rc-stat-value">{r.n_supply_ids}</span>
                </div>
                <div className="rc-stat">
                  <span className="rc-stat-label">Repeating</span>
                  <span className="rc-stat-value">{r.n_repeating}</span>
                </div>
                <div className="rc-stat">
                  <span className="rc-stat-label">One-time</span>
                  <span className="rc-stat-value">{r.n_one_time}</span>
                </div>
                <div className="rc-stat">
                  <span className="rc-stat-label">Offers</span>
                  <span className="rc-stat-value">{r.total_supply_offers}</span>
                </div>
                <div className="rc-stat">
                  <span className="rc-stat-label">One-time %</span>
                  <span className="rc-stat-value">{fmtPct(r.one_time_pct)}</span>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Per-region retention bar chart (simple horizontal bars) */}
      {hasData && (
        <div className="region-chart">
          <h4 className="section-subheader">Retention comparison</h4>
          <div className="region-bars">
            {data.regions.map((r) => (
              <div key={r.region} className="region-bar-row">
                <div className="region-bar-label">
                  <span className="region-bar-emoji">{r.region_emoji}</span>
                  <span className="region-bar-name">{r.region_name}</span>
                </div>
                <div className="region-bar-track">
                  <div
                    className="region-bar-fill"
                    style={{
                      width: `${Math.max(r.retention_rate_pct, 1)}%`,
                      backgroundColor: retentionColor(r.retention_rate_pct),
                    }}
                  />
                  <span className="region-bar-pct">{fmtPct(r.retention_rate_pct)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Detail table */}
      {hasData && (
        <div className="region-table-wrapper">
          <h4 className="section-subheader">Detail table</h4>
          <table className="data-table region-table">
            <thead>
              <tr>
                <th>Region</th>
                <th>Supplies</th>
                <th>Repeating</th>
                <th>One-time</th>
                <th>Offers</th>
                <th>Retention</th>
                <th>One-time %</th>
              </tr>
            </thead>
            <tbody>
              {data.regions.filter(r => r.n_supply_ids > 0).map((r) => (
                <tr key={r.region}>
                  <td>
                    <span className="region-cell-emoji">{r.region_emoji}</span> {r.region_name}
                  </td>
                  <td>{r.n_supply_ids}</td>
                  <td>{r.n_repeating}</td>
                  <td>{r.n_one_time}</td>
                  <td>{r.total_supply_offers}</td>
                  <td style={{ color: retentionColor(r.retention_rate_pct) }}>
                    {fmtPct(r.retention_rate_pct)}
                  </td>
                  <td>{fmtPct(r.one_time_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-footnote">
        Total dedup supply IDs: {data.total_supply_ids}
        {data.material_type_filter && ` · Filter: ${data.material_type_filter}`}
        {data.n_unknown_with_data > 0 && ` · ${data.n_unknown_with_data} unknown-coord supplies (excluded from real cities)`}
        {' · '}
        City assignment: {data.city_assignment_method}
        {' · '}
        iter #73 · auto-refresh 60s
      </div>
    </div>
  )
}

function regionMeta(region) {
  return {
    emoji: { 'Göteborg': '⚓', 'Borås': '🧵', 'Stockholm': '🏛️', 'unknown': '❓' }[region] || '📍',
  }
}