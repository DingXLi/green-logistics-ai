/**
 * CycleKpiSummary - 总 KPI 摘要 + best/worst/last cycle (iter #16)
 *
 * 数据源: GET /api/persistence/cycle-kpi-summary
 *
 * 显示:
 * - Total cycles / cycles_with_matches
 * - Total tons / distance / co2 / cost
 * - Avg tons per cycle, cost per ton, co2 per ton
 * - Fleet utilization avg
 * - Sim day range
 * - Best cycle / Worst cycle / Last cycle 卡片
 */

import { useState, useEffect } from 'react'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

export function CycleKpiSummary() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/persistence/cycle-kpi-summary`)
      .then(r => r.json())
      .then(d => {
        if (!cancelled) {
          setData(d)
          setLoading(false)
        }
      })
      .catch(e => {
        if (!cancelled) {
          setError(e.message)
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [])

  if (loading) return <LoadingSpinner label="Loading cycle KPI summary…" />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data || data.total_cycles === 0) {
    return <div className="empty">No cycle data yet. Run a cycle first.</div>
  }

  return (
    <div className="chart-card">
      <h3>📈 Cycle KPI Summary</h3>
      <p className="chart-subtitle">
        Aggregate KPIs across all {data.total_cycles} simulation cycles
        (day {data.sim_day_range?.min ?? '?'} – {data.sim_day_range?.max ?? '?'}).
      </p>

      {/* Top stat strip */}
      <div className="agg-totals">
        <div className="agg-stat">
          <div className="agg-stat-value">{data.total_cycles}</div>
          <div className="agg-stat-label">Total cycles</div>
        </div>
        <div className="agg-stat">
          <div className="agg-stat-value">{data.n_cycles_with_matches}</div>
          <div className="agg-stat-label">Cycles w/ matches</div>
        </div>
        <div className="agg-stat">
          <div className="agg-stat-value">{data.total_tons_matched?.toFixed(1) ?? '—'}</div>
          <div className="agg-stat-label">Total tons</div>
        </div>
        <div className="agg-stat">
          <div className="agg-stat-value">{data.total_distance_km?.toFixed(0) ?? '—'}</div>
          <div className="agg-stat-label">Total km</div>
        </div>
        <div className="agg-stat">
          <div className="agg-stat-value">{data.total_co2_kg?.toFixed(0) ?? '—'}</div>
          <div className="agg-stat-label">Total CO₂ (kg)</div>
        </div>
        <div className="agg-stat">
          <div className="agg-stat-value">{data.total_cost_sek?.toFixed(0) ?? '—'}</div>
          <div className="agg-stat-label">Total SEK</div>
        </div>
      </div>

      {/* Per-cycle averages */}
      <div className="kpi-row">
        <div className="kpi-mini-card">
          <div className="kpi-mini-label">Avg tons/cycle</div>
          <div className="kpi-mini-value">
            {data.avg_tons_per_cycle?.toFixed(2) ?? '—'}
          </div>
        </div>
        <div className="kpi-mini-card">
          <div className="kpi-mini-label">Cost / ton</div>
          <div className="kpi-mini-value">
            {data.avg_cost_per_ton_sek != null
              ? `${data.avg_cost_per_ton_sek.toFixed(1)} SEK`
              : '—'}
          </div>
        </div>
        <div className="kpi-mini-card">
          <div className="kpi-mini-label">CO₂ / ton</div>
          <div className="kpi-mini-value">
            {data.avg_co2_per_ton_kg != null
              ? `${data.avg_co2_per_ton_kg.toFixed(2)} kg`
              : '—'}
          </div>
        </div>
        <div className="kpi-mini-card">
          <div className="kpi-mini-label">Fleet util avg</div>
          <div className="kpi-mini-value">
            {data.fleet_utilization_avg_pct?.toFixed(1) ?? '—'}%
          </div>
        </div>
      </div>

      {/* Best / Worst / Last cycle cards */}
      <div className="cycle-cards">
        <CycleCard kind="best" cycle={data.best_cycle} />
        <CycleCard kind="worst" cycle={data.worst_cycle} />
        <CycleCard kind="last" cycle={data.last_cycle} />
      </div>
    </div>
  )
}

function CycleCard({ kind, cycle }) {
  if (!cycle) return null
  const colors = {
    best:  { bg: '#dcfce7', accent: '#22c55e', icon: '🏆', label: 'Best cycle' },
    worst: { bg: '#fee2e2', accent: '#ef4444', icon: '⚠️', label: 'Worst cycle' },
    last:  { bg: '#dbeafe', accent: '#3b82f6', icon: '🕐', label: 'Last cycle' },
  }
  const c = colors[kind]
  return (
    <div className="cycle-card" style={{ backgroundColor: c.bg, borderLeft: `4px solid ${c.accent}` }}>
      <div className="cycle-card-header">
        <span className="cycle-card-icon">{c.icon}</span>
        <span className="cycle-card-label">{c.label}</span>
      </div>
      <div className="cycle-card-cycle-id">{cycle.cycle_id}</div>
      <div className="cycle-card-stats">
        <span>Day {cycle.sim_day}</span>
        <span>·</span>
        <span><strong>{cycle.total_tons?.toFixed(1) ?? '—'}</strong> t</span>
        {cycle.n_matches != null && (
          <>
            <span>·</span>
            <span>{cycle.n_matches} matches</span>
          </>
        )}
      </div>
    </div>
  )
}

export default CycleKpiSummary