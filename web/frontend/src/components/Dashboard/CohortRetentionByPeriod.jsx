/**
 * CohortRetentionByPeriod - 按时段看 retention 趋势
 *   (iter #20 + iter #24 unit + iter #25 deep link + iter #46 material filter)
 *
 * 数据源: GET /api/persistence/cohort-retention-by-period?n_periods=4&period_unit=quartile[&material_type=concrete]
 *
 * 显示:
 * - period_unit (iter #24): quartile / day / week / month
 * - n_periods: 可配置 (1-30/52/12/10 per unit)
 * - material_type (iter #46): optional filter to single material
 * - trend badge: improving / declining / stable / unknown
 * - bar visualization (recharts) 显示每段 retention_rate_pct
 * - 每段详细 KPI (n_supply_ids / n_one_time / n_repeating / one_time_pct)
 *
 * URL state (iter #25 + iter #46): ?period_unit=week&n_periods=8&material_type=concrete
 * - 可分享: 用户粘贴 URL 给同事, 自动恢复 cohort view 配置
 * - 可收藏: bookmark 当前 view 不用再选
 * - back/forward 工作
 * - iter #46 新增 material_type 入 URL state, 与 crosstab 保持一致
 */

import { useState, useEffect } from 'react'
import { useUrlState } from '../../hooks/useUrlState'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const TREND_COLORS = {
  improving: { bg: '#dcfce7', accent: '#22c55e', icon: '📈', label: 'Improving' },
  declining: { bg: '#fee2e2', accent: '#ef4444', icon: '📉', label: 'Declining' },
  stable:    { bg: '#fef9c3', accent: '#f59e0b', icon: '⚖️', label: 'Stable' },
  unknown:   { bg: '#f3f4f6', accent: '#9ca3af', icon: '❓', label: 'Unknown' },
}

function trendColor(rate) {
  if (rate == null) return '#9ca3af'
  if (rate >= 70) return '#22c55e'
  if (rate >= 30) return '#f59e0b'
  return '#ef4444'
}

export function CohortRetentionByPeriod() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  // iter #25: URL-synced state for deep linking
  const [periodUnit, setPeriodUnit] = useUrlState('period_unit', 'quartile')  // iter #24 + iter #25
  const [nPeriods, setNPeriods] = useUrlState('n_periods', 4, 'int')  // iter #25
  // iter #46: material filter synced to URL too (consistency with crosstab)
  const [materialType, setMaterialType] = useUrlState('material_type', '')
  const [expandedPeriod, setExpandedPeriod] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    // iter #46: append material_type to query when non-empty
    const matParam = materialType
      ? `&material_type=${encodeURIComponent(materialType)}`
      : ''
    fetch(`${API_BASE}/persistence/cohort-retention-by-period?n_periods=${nPeriods}&period_unit=${periodUnit}${matParam}`)
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
  }, [nPeriods, periodUnit, materialType])

  if (loading) return <LoadingSpinner label={`Loading cohort retention (${nPeriods} ${periodUnit} periods)…`} />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data) return <div className="empty">No cohort retention data.</div>

  const trend = TREND_COLORS[data.trend] || TREND_COLORS.unknown

  // Empty state: not enough cycles
  if (!data.periods || data.periods.length === 0) {
    return (
      <div className="chart-card">
        <h3>📊 Cohort Retention by Period</h3>
        <p className="chart-subtitle">
          Early vs late retention comparison (split into {nPeriods} periods).
        </p>
        <div className="empty">
          Need at least {nPeriods} cycles across multiple sim_days to compute period breakdown.
          Currently: {data.total_supply_ids} unique supplies tracked.
        </div>
      </div>
    )
  }

  // Prepare chart data
  const chartData = data.periods.map(p => ({
    name: `P${p.period_idx}`,
    period_label: p.period_label,
    sim_day: `${p.sim_day_range.min}-${p.sim_day_range.max}`,
    retention_pct: p.retention_rate_pct,
    one_time_pct: p.one_time_pct,
    n_supplies: p.n_supply_ids,
  }))

  return (
    <div className="chart-card">
      <h3>📊 Cohort Retention by Period</h3>
      <p className="chart-subtitle">
        Split {data.total_supply_ids} supplies across {data.periods.length} time periods.
        Compare early vs late retention to detect churn trends.
        {materialType && (
          <> · filtered to material <code>{materialType}</code></>
        )}
      </p>

      {/* Trend badge + controls */}
      <div className="cohort-controls">
        <div className="cohort-trend-badge" style={{ backgroundColor: trend.bg, borderLeft: `4px solid ${trend.accent}` }}>
          <span className="cohort-trend-icon">{trend.icon}</span>
          <span className="cohort-trend-label">{trend.label}</span>
        </div>
        {/* iter #46: per-material trend badges (only when no material filter) */}
        {!materialType && data.trend_per_material && Object.keys(data.trend_per_material).length > 0 && (
          <div className="cohort-material-trends">
            {Object.entries(data.trend_per_material).map(([mat, matTrend]) => {
              const mt = TREND_COLORS[matTrend] || TREND_COLORS.unknown
              return (
                <div
                  key={mat}
                  className="cohort-material-trend-pill"
                  style={{ backgroundColor: mt.bg, borderLeft: `3px solid ${mt.accent}` }}
                  title={`${mat} trend: ${mt.label}`}
                >
                  <span className="cohort-material-trend-icon">{mt.icon}</span>
                  <span className="cohort-material-trend-name">{mat}</span>
                </div>
              )
            })}
          </div>
        )}
        <label className="cohort-periods-label">
          Unit:
          <select
            className="cohort-periods-select"
            value={periodUnit}
            onChange={e => {
              const newUnit = e.target.value
              setPeriodUnit(newUnit)
              // Reset nPeriods to sensible default for unit
              if (newUnit === 'month') setNPeriods(6)
              else if (newUnit === 'week') setNPeriods(8)
              else if (newUnit === 'day') setNPeriods(14)
              else setNPeriods(4)
            }}
          >
            <option value="quartile">Quartile (equal split)</option>
            <option value="week">Week (7 sim_days)</option>
            <option value="day">Day (1 sim_day)</option>
            <option value="month">Month (30 sim_days)</option>
          </select>
        </label>
        <label className="cohort-periods-label">
          Periods:
          <select
            className="cohort-periods-select"
            value={nPeriods}
            onChange={e => setNPeriods(parseInt(e.target.value) || 4)}
          >
            {periodUnit === 'day' && (
              <>
                <option value="7">7</option>
                <option value="14">14</option>
                <option value="21">21</option>
                <option value="30">30</option>
              </>
            )}
            {periodUnit === 'week' && (
              <>
                <option value="4">4</option>
                <option value="8">8</option>
                <option value="12">12</option>
                <option value="26">26</option>
                <option value="52">52</option>
              </>
            )}
            {periodUnit === 'month' && (
              <>
                <option value="3">3</option>
                <option value="6">6</option>
                <option value="12">12</option>
              </>
            )}
            {periodUnit === 'quartile' && (
              <>
                <option value="2">2 (halves)</option>
                <option value="3">3 (thirds)</option>
                <option value="4">4 (quartiles)</option>
                <option value="6">6</option>
                <option value="8">8</option>
              </>
            )}
          </select>
        </label>
        {/* iter #46: material type filter (consistency with crosstab) */}
        <label className="cohort-periods-label">
          Material:
          <input
            type="text"
            className="cohort-periods-select"
            placeholder="(all)"
            value={materialType}
            onChange={e => setMaterialType(e.target.value.trim())}
            style={{ width: '110px' }}
          />
        </label>
        {materialType && (
          <button
            className="cohort-periods-select"
            style={{ background: '#334155', color: '#e2e8f0', border: '1px solid #475569', cursor: 'pointer' }}
            onClick={() => setMaterialType('')}
          >
            clear
          </button>
        )}
      </div>

      {/* Bar chart */}
      <ResponsiveContainer width="100%" height={250}>
        <BarChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" label={{ value: 'Period', position: 'insideBottom', offset: -5 }} />
          <YAxis label={{ value: 'Retention %', angle: -90, position: 'insideLeft' }} domain={[0, 100]} />
          <Tooltip content={({ active, payload }) => {
            if (!active || !payload || !payload.length) return null
            const p = payload[0].payload
            return (
              <div className="custom-tooltip">
                <div><strong>{p.period_label}</strong></div>
                <div>sim_day: {p.sim_day}</div>
                <div>Retention: {p.retention_pct.toFixed(1)}%</div>
                <div>One-time: {p.one_time_pct.toFixed(1)}%</div>
                <div>Supplies: {p.n_supplies}</div>
              </div>
            )
          }} />
          <Legend />
          <Bar dataKey="retention_pct" fill="#22c55e" name="Retention %" />
          <Bar dataKey="one_time_pct" fill="#ef4444" name="One-time %" />
        </BarChart>
      </ResponsiveContainer>

      {/* Period cards */}
      <div className="cohort-period-cards">
        {data.periods.map(p => {
          const isExpanded = expandedPeriod === p.period_idx
          return (
            <div
              key={p.period_idx}
              className={`cohort-period-card ${isExpanded ? 'expanded' : ''}`}
              onClick={() => setExpandedPeriod(isExpanded ? null : p.period_idx)}
            >
              <div className="cohort-period-header">
                <span className="cohort-period-idx">Period {p.period_idx}</span>
                <span className="cohort-period-days">
                  sim_day {p.sim_day_range.min}-{p.sim_day_range.max}
                </span>
              </div>
              <div className="cohort-period-retention" style={{ color: trendColor(p.retention_rate_pct) }}>
                {p.retention_rate_pct.toFixed(1)}%
              </div>
              <div className="cohort-period-meta">
                {p.n_supply_ids} supplies · {p.n_repeating} repeat · {p.n_one_time} one-time
              </div>
              {isExpanded && (
                <div className="cohort-period-details">
                  <div className="cohort-detail-row">
                    <span>One-time %:</span>
                    <strong>{p.one_time_pct.toFixed(1)}%</strong>
                  </div>
                  <div className="cohort-detail-row">
                    <span>Repeating %:</span>
                    <strong>{p.retention_rate_pct.toFixed(1)}%</strong>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className="cohort-footnote">
        Click a period card for details. Trend compares first vs last period retention
        (threshold ±5%).
      </div>
    </div>
  )
}

export default CohortRetentionByPeriod