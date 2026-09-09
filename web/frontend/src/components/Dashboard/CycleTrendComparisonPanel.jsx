/**
 * CycleTrendComparisonPanel - early-window vs late-window cycle comparison (iter #65)
 *
 * 数据源:
 *   GET /api/persistence/cycle-trend-comparison
 *
 * 显示:
 * - Metric selector (7 efficiency metrics)
 * - Early/late window size inputs
 * - Sim_day window filter
 * - Side-by-side KPI cards (early vs late)
 * - Delta + pct change visualization
 * - Trend badge (improving / declining / stable)
 *
 * 用途: 让用户看到:
 *       - 系统 metric 在 simulation 期间是改善还是变差
 *       - 改善的幅度 (% change)
 *       - 早期 vs 晚期 cycle 的 cycle_id 列表
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const METRICS = [
  { key: 'co2_per_ton', label: '🌱 CO₂ / ton', unit: 'kg/t', lower_better: true },
  { key: 'cost_per_ton', label: '💰 Cost / ton', unit: 'SEK/t', lower_better: true },
  { key: 'co2_per_km', label: '🌱 CO₂ / km', unit: 'kg/km', lower_better: true },
  { key: 'cost_per_km', label: '💰 Cost / km', unit: 'SEK/km', lower_better: true },
  { key: 'fleet_utilization_pct', label: '⚡ Fleet util', unit: '%', lower_better: false },
  { key: 'match_rate_vs_offers', label: '✅ Match rate', unit: 'm/off', lower_better: false },
  { key: 'tons_per_cycle', label: '📦 Tons / cycle', unit: 't', lower_better: false },
]

function trendColor(trend) {
  if (trend === 'improving') return '#22c55e'
  if (trend === 'declining') return '#ef4444'
  return '#64748b'
}

function fmt(value, key) {
  if (value === null || value === undefined) return '—'
  if (key === 'match_rate_vs_offers') return value.toFixed(3)
  if (key === 'fleet_utilization_pct') return value.toFixed(1)
  return value.toFixed(3)
}

export function CycleTrendComparisonPanel() {
  const [metric, setMetric] = useState('co2_per_ton')
  const [earlyWindow, setEarlyWindow] = useState(5)
  const [lateWindow, setLateWindow] = useState(5)
  const [sinceDay, setSinceDay] = useState('')
  const [untilDay, setUntilDay] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async (
    m = metric, ew = earlyWindow, lw = lateWindow, sd = sinceDay, ud = untilDay
  ) => {
    try {
      const params = new URLSearchParams({
        metric: m,
        early_window: String(ew),
        late_window: String(lw),
      })
      if (sd !== '') params.set('since_sim_day', String(sd))
      if (ud !== '') params.set('until_sim_day', String(ud))
      const res = await fetch(`${API_BASE}/persistence/cycle-trend-comparison?${params}`)
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
      fetchData(metric, earlyWindow, lateWindow, sinceDay, untilDay)
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    fetchData(metric, earlyWindow, lateWindow, sinceDay, untilDay)
  }, [metric, earlyWindow, lateWindow, sinceDay, untilDay])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading cycle trend…" />
  }

  if (error || !data) {
    return (
      <div className="card cycle-trend-panel">
        <h3>📈 Cycle Trend Comparison</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No cycle data yet.'}
        </div>
      </div>
    )
  }

  const metricDef = METRICS.find((m) => m.key === metric)
  const early = data.early || {}
  const late = data.late || {}
  const delta = data.delta || {}
  const trend = data.trend || 'stable'

  return (
    <div className="card cycle-trend-panel">
      <div className="card-header-row">
        <h3>📈 Cycle Trend Comparison</h3>
        <div className="card-controls">
          <span
            className="trend-badge trend-badge-large"
            style={{
              backgroundColor: trendColor(trend),
              color: 'white',
            }}
          >
            {trend === 'improving' ? '↗ Improving' :
             trend === 'declining' ? '↘ Declining' : '→ Stable'}
          </span>
        </div>
      </div>

      <div className="filter-row">
        <label>Metric:</label>
        <select
          className="filter-select"
          value={metric}
          onChange={(e) => setMetric(e.target.value)}
        >
          {METRICS.map((m) => (
            <option key={m.key} value={m.key}>{m.label}</option>
          ))}
        </select>

        <label>Early N:</label>
        <input
          type="number"
          className="filter-input filter-input-narrow"
          min="1"
          max="50"
          value={earlyWindow}
          onChange={(e) => setEarlyWindow(Number(e.target.value) || 1)}
        />

        <label>Late N:</label>
        <input
          type="number"
          className="filter-input filter-input-narrow"
          min="1"
          max="50"
          value={lateWindow}
          onChange={(e) => setLateWindow(Number(e.target.value) || 1)}
        />

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

      <div className="trend-grid">
        <div className="trend-card trend-card-early">
          <div className="trend-card-label">
            Early window ({early.n_cycles || 0} cycles)
          </div>
          <div className="trend-card-value">
            {fmt(early.mean_value, metric)}{' '}
            <span className="trend-card-unit">{metricDef?.unit || ''}</span>
          </div>
          <div className="trend-card-stats">
            <span>min {fmt(early.min_value, metric)}</span>
            <span>max {fmt(early.max_value, metric)}</span>
            <span>σ {fmt(early.stddev_value, metric)}</span>
          </div>
          <div className="trend-card-cycles">
            {(early.cycle_ids || []).slice(0, 5).map((c) => (
              <span key={c} className="cycle-chip">{c}</span>
            ))}
            {early.cycle_ids && early.cycle_ids.length > 5 && (
              <span className="cycle-chip-more">+{early.cycle_ids.length - 5}</span>
            )}
          </div>
        </div>

        <div className="trend-arrow">
          <div className="trend-arrow-icon">
            {data.direction === 'lower_is_better' ? '↓' : '↑'}{' '}
            {delta.absolute !== null
              ? (delta.absolute >= 0 ? '+' : '') + fmt(delta.absolute, metric)
              : '—'}
          </div>
          <div className="trend-arrow-pct">
            {delta.pct_change !== null
              ? (delta.pct_change >= 0 ? '+' : '') + delta.pct_change.toFixed(2) + '%'
              : '—'}
          </div>
        </div>

        <div className="trend-card trend-card-late">
          <div className="trend-card-label">
            Late window ({late.n_cycles || 0} cycles)
          </div>
          <div className="trend-card-value">
            {fmt(late.mean_value, metric)}{' '}
            <span className="trend-card-unit">{metricDef?.unit || ''}</span>
          </div>
          <div className="trend-card-stats">
            <span>min {fmt(late.min_value, metric)}</span>
            <span>max {fmt(late.max_value, metric)}</span>
            <span>σ {fmt(late.stddev_value, metric)}</span>
          </div>
          <div className="trend-card-cycles">
            {(late.cycle_ids || []).slice(0, 5).map((c) => (
              <span key={c} className="cycle-chip">{c}</span>
            ))}
            {late.cycle_ids && late.cycle_ids.length > 5 && (
              <span className="cycle-chip-more">+{late.cycle_ids.length - 5}</span>
            )}
          </div>
        </div>
      </div>

      <div className="card-footnote">
        Direction:{' '}
        <strong>
          {data.direction === 'lower_is_better' ? '↓ lower = better' : '↑ higher = better'}
        </strong>
        {' · '}
        Window: {data.window?.since_sim_day ?? '∞'} →{' '}
        {data.window?.until_sim_day ?? '∞'}
        {' · '}
        iter #65 · auto-refresh 60s
      </div>
    </div>
  )
}