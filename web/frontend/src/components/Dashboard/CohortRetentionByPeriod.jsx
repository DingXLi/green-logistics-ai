/**
 * CohortRetentionByPeriod - 按时段看 retention 趋势 (iter #20)
 *
 * 数据源: GET /api/persistence/cohort-retention-by-period?n_periods=4
 *
 * 显示:
 * - 4 个 period (默认 quartile) 的 retention rate 对比
 * - trend badge: improving / declining / stable / unknown
 * - bar visualization (recharts) 显示每段 retention_rate_pct
 * - 每段详细 KPI (n_supply_ids / n_one_time / n_repeating / one_time_pct)
 *
 * 用途:
 * - 看 retention 是否随时间 改善 / 退化 / 稳定
 * - 早期 vs 后期 churn 趋势对比
 */

import { useState, useEffect } from 'react'
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
  const [nPeriods, setNPeriods] = useState(4)
  const [periodUnit, setPeriodUnit] = useState('quartile')  // iter #24
  const [expandedPeriod, setExpandedPeriod] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch(`${API_BASE}/persistence/cohort-retention-by-period?n_periods=${nPeriods}&period_unit=${periodUnit}`)
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
  }, [nPeriods, periodUnit])

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
      </p>

      {/* Trend badge + controls */}
      <div className="cohort-controls">
        <div className="cohort-trend-badge" style={{ backgroundColor: trend.bg, borderLeft: `4px solid ${trend.accent}` }}>
          <span className="cohort-trend-icon">{trend.icon}</span>
          <span className="cohort-trend-label">{trend.label}</span>
        </div>
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