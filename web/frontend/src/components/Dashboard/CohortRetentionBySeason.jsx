/**
 * CohortRetentionBySeason — supply retention broken down by SEASON (iter #70)
 *
 * 数据源:
 *   GET /api/persistence/cohort-retention-by-season
 *
 * 显示:
 * - 4 season cards (winter ❄️ / spring 🌱 / summer ☀️ / fall 🍂)
 *   卡片: months / n_supply_ids / n_repeating / retention_rate_pct / one_time_pct
 * - Best/worst season badge + Δ% (e.g. "winter is +32% better than summer")
 * - Per-season retention bar chart
 * - Summary table: season / n_supply_ids / n_cycles / offers / retention
 * - material_type filter
 * - 60s auto-refresh
 *
 * 用途: 让 ops 看 retention 模式是否随季节波动 (Sweden 季节性明显)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

// Season colors — cold→warm to match Sweden climate
const SEASON_COLORS = {
  winter: '#60a5fa',  // blue (cold)
  spring: '#22c55e',  // green (growth)
  summer: '#f97316',  // orange (warm)
  fall:   '#a16207',  // brown (leaves)
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

function fmtMonths(months) {
  return months.map(m => {
    const names = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    return names[m]
  }).join('/')
}

export function CohortRetentionBySeason() {
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
        const url = `${API_BASE}/persistence/cohort-retention-by-season${
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
      <div className="card cohort-retention-by-season">
        <LoadingSpinner label="Loading cohort retention by season…" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="card cohort-retention-by-season">
        <div className="error-banner">⚠ {error}</div>
      </div>
    )
  }

  if (!data) return null

  const hasData = data.n_seasons_with_data > 0

  return (
    <div className="card cohort-retention-by-season">
      <div className="card-header">
        <h3>🌦️ Cohort Retention by Season <span className="card-subtitle">(iter #70)</span></h3>
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

      {/* Best/Worst season badge */}
      {hasData && data.best_season && data.best_season !== data.worst_season && (
        <div className="season-extreme-badge">
          {SEASON_NAMES[data.best_season]?.emoji} <strong>{SEASON_NAMES[data.best_season]?.name}</strong> retention
          ({fmtPct(data.best_season_pct)}) is better than{' '}
          {SEASON_NAMES[data.worst_season]?.emoji} <strong>{SEASON_NAMES[data.worst_season]?.name}</strong>{' '}
          ({fmtPct(data.worst_season_pct)}) — Δ {fmtPct(data.worst_vs_best_pct)}
        </div>
      )}
      {hasData && data.best_season === data.worst_season && (
        <div className="season-extreme-badge tied">
          All seasons with data tied at {fmtPct(data.best_season_pct)} retention
        </div>
      )}

      {!hasData && (
        <div className="empty-state">
          No cohort retention data for selected material filter. Try removing the
          filter to see all supplies.
        </div>
      )}

      {/* Season KPI grid */}
      <div className="season-kpi-row">
        {data.seasons.map((s) => {
          const isBest = s.season === data.best_season
          const isWorst = s.season === data.worst_season && data.best_season !== data.worst_season
          const borderColor = SEASON_COLORS[s.season]
          return (
            <div
              key={s.season}
              className={`season-card ${isBest ? 'best' : ''} ${isWorst ? 'worst' : ''} ${s.n_supply_ids === 0 ? 'empty' : ''}`}
              style={{ borderColor }}
            >
              <div className="season-card-header">
                <span className="season-emoji-large">{s.season_emoji}</span>
                <div className="season-card-title">
                  <div className="season-name">{s.season_name}</div>
                  <div className="season-months">{fmtMonths(s.months)}</div>
                </div>
                {isBest && <span className="season-tag best-tag">Best</span>}
                {isWorst && <span className="season-tag worst-tag">Worst</span>}
              </div>

              <div className="season-card-retention">
                <span
                  className="retention-value"
                  style={{ color: retentionColor(s.retention_rate_pct) }}
                >
                  {fmtPct(s.retention_rate_pct)}
                </span>
                <span className="retention-label">retention</span>
              </div>

              <div className="season-card-bar">
                <div
                  className="season-card-bar-fill"
                  style={{
                    width: `${s.retention_rate_pct}%`,
                    backgroundColor: retentionColor(s.retention_rate_pct),
                  }}
                />
              </div>

              <div className="season-card-stats">
                <div className="sc-stat">
                  <span className="sc-stat-label">Supplies</span>
                  <span className="sc-stat-value">{s.n_supply_ids}</span>
                </div>
                <div className="sc-stat">
                  <span className="sc-stat-label">Repeating</span>
                  <span className="sc-stat-value">{s.n_repeating}</span>
                </div>
                <div className="sc-stat">
                  <span className="sc-stat-label">One-time</span>
                  <span className="sc-stat-value">{s.n_one_time}</span>
                </div>
                <div className="sc-stat">
                  <span className="sc-stat-label">Cycles</span>
                  <span className="sc-stat-value">{s.n_cycles_in_season}</span>
                </div>
                <div className="sc-stat">
                  <span className="sc-stat-label">Offers</span>
                  <span className="sc-stat-value">{s.total_supply_offers}</span>
                </div>
                <div className="sc-stat">
                  <span className="sc-stat-label">One-time %</span>
                  <span className="sc-stat-value">{fmtPct(s.one_time_pct)}</span>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Per-season retention bar chart (simple horizontal bars) */}
      {hasData && (
        <div className="season-chart">
          <h4 className="section-subheader">Retention comparison</h4>
          <div className="season-bars">
            {data.seasons.map((s) => (
              <div key={s.season} className="season-bar-row">
                <div className="season-bar-label">
                  <span className="season-bar-emoji">{s.season_emoji}</span>
                  <span className="season-bar-name">{s.season_name}</span>
                </div>
                <div className="season-bar-track">
                  <div
                    className="season-bar-fill"
                    style={{
                      width: `${Math.max(s.retention_rate_pct, 1)}%`,
                      backgroundColor: retentionColor(s.retention_rate_pct),
                    }}
                  />
                  <span className="season-bar-pct">{fmtPct(s.retention_rate_pct)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Detail table */}
      {hasData && (
        <div className="season-table-wrapper">
          <h4 className="section-subheader">Detail table</h4>
          <table className="data-table season-table">
            <thead>
              <tr>
                <th>Season</th>
                <th>Months</th>
                <th>Supplies</th>
                <th>Repeating</th>
                <th>One-time</th>
                <th>Cycles</th>
                <th>Offers</th>
                <th>Retention</th>
                <th>One-time %</th>
              </tr>
            </thead>
            <tbody>
              {data.seasons.filter(s => s.n_supply_ids > 0).map((s) => (
                <tr key={s.season}>
                  <td>
                    <span className="season-cell-emoji">{s.season_emoji}</span> {s.season_name}
                  </td>
                  <td>{fmtMonths(s.months)}</td>
                  <td>{s.n_supply_ids}</td>
                  <td>{s.n_repeating}</td>
                  <td>{s.n_one_time}</td>
                  <td>{s.n_cycles_in_season}</td>
                  <td>{s.total_supply_offers}</td>
                  <td style={{ color: retentionColor(s.retention_rate_pct) }}>
                    {fmtPct(s.retention_rate_pct)}
                  </td>
                  <td>{fmtPct(s.one_time_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-footnote">
        Total dedup supply IDs: {data.total_supply_ids}
        {data.material_type_filter && ` · Filter: ${data.material_type_filter}`}
        {' · '}
        iter #70 · auto-refresh 60s
      </div>
    </div>
  )
}

// Convenience — inject at top of file for badge rendering
const SEASON_NAMES = {
  winter: { name: 'Winter', emoji: '❄️' },
  spring: { name: 'Spring', emoji: '🌱' },
  summer: { name: 'Summer', emoji: '☀️' },
  fall:   { name: 'Fall',   emoji: '🍂' },
}

export default CohortRetentionBySeason