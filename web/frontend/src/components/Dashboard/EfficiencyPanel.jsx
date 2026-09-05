/**
 * EfficiencyPanel - per-ton / per-match efficiency trends (iter #52)
 *
 * 数据源: GET /api/persistence/kpi-timeseries
 *
 * 显示:
 * - 4 KPI 卡片: cost_per_ton, co2_per_ton, cost_per_match, supply_demand_ratio
 * - Recharts LineChart: 30 天 efficiency 趋势 (cost/ton, co2/ton)
 * - 表 (最新一天 vs 平均值 vs 最佳一天)
 *
 * 用途: 让用户看到:
 *       - 运输每吨成本是否在改善 (成本下降 = 效率提升)
 *       - 运输每吨 CO2 是否在降低 (环保进步)
 *       - 供需匹配率是否提高
 */

import { useState, useEffect, useMemo } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

function _safeNum(v, decimals = 2) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(decimals)
}

function _trendColor(value, threshold_high, threshold_low) {
  // Higher is "good" for some metrics, lower is "good" for others
  // Generic: green if above threshold_high, red if below threshold_low
  if (value === null || value === undefined) return '#64748b'
  if (value >= threshold_high) return '#16a34a'
  if (value <= threshold_low) return '#dc2626'
  return '#f59e0b'
}

export function EfficiencyPanel({ windowSize = 30 }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async () => {
    try {
      setLoading(true)
      const res = await fetch(`${API_BASE}/persistence/kpi-timeseries`)
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
    const id = setInterval(fetchData, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  // 计算趋势 (last vs first) — 注意 first 可能是 null
  const stats = useMemo(() => {
    if (!data || data.length === 0) return null
    const valid = (k) => data.filter(d => d[k] != null)
    const avg = (k) => {
      const v = valid(k)
      return v.length === 0 ? null : v.reduce((s, d) => s + d[k], 0) / v.length
    }
    const last = (k) => {
      const v = valid(k)
      return v.length === 0 ? null : v[v.length - 1][k]
    }
    const best = (k, mode = 'min') => {
      const v = valid(k)
      if (v.length === 0) return null
      return mode === 'min'
        ? v.reduce((a, b) => (b[k] < a[k] ? b : a))[k]
        : v.reduce((a, b) => (b[k] > a[k] ? b : a))[k]
    }

    return {
      n_days: data.length,
      cost_per_ton_avg: avg('cost_per_ton_sek'),
      cost_per_ton_last: last('cost_per_ton_sek'),
      cost_per_ton_best: best('cost_per_ton_sek', 'min'),
      co2_per_ton_avg: avg('co2_per_ton_kg'),
      co2_per_ton_last: last('co2_per_ton_kg'),
      co2_per_ton_best: best('co2_per_ton_kg', 'min'),
      ratio_avg: avg('supply_demand_ratio'),
      ratio_last: last('supply_demand_ratio'),
      ratio_best: best('supply_demand_ratio', 'max'),
    }
  }, [data])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading efficiency metrics…" />
  }

  if (error || !data || data.length === 0) {
    return (
      <div className="card efficiency-panel">
        <h3>📊 Efficiency Metrics (iter #52)</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No data yet. Run more simulations to see efficiency trends.'}
        </div>
      </div>
    )
  }

  const chartData = data.slice(-windowSize).map(d => ({
    day: `D${d.sim_day}`,
    cost_per_ton: d.cost_per_ton_sek,
    co2_per_ton: d.co2_per_ton_kg,
    ratio: d.supply_demand_ratio != null ? d.supply_demand_ratio * 100 : null,  // 0-100 for chart
  }))

  return (
    <div className="card efficiency-panel">
      <h3>📊 Efficiency Metrics</h3>
      <div className="card-subtitle">
        Per-ton / per-match efficiency across {stats.n_days} day{stats.n_days !== 1 ? 's' : ''}
      </div>

      <div className="kpi-grid">
        <div className="kpi-card" style={{ borderTop: '3px solid #ef4444' }}>
          <div className="kpi-label">Cost / ton (avg)</div>
          <div className="kpi-value">
            {_safeNum(stats.cost_per_ton_avg, 2)}
            <span className="kpi-unit"> SEK/t</span>
          </div>
          <div className="kpi-sub">
            best: {_safeNum(stats.cost_per_ton_best, 2)} · last: {_safeNum(stats.cost_per_ton_last, 2)}
          </div>
        </div>

        <div className="kpi-card" style={{ borderTop: '3px solid #f59e0b' }}>
          <div className="kpi-label">CO₂ / ton (avg)</div>
          <div className="kpi-value">
            {_safeNum(stats.co2_per_ton_avg, 2)}
            <span className="kpi-unit"> kg/t</span>
          </div>
          <div className="kpi-sub">
            best: {_safeNum(stats.co2_per_ton_best, 2)} · last: {_safeNum(stats.co2_per_ton_last, 2)}
          </div>
        </div>

        <div className="kpi-card" style={{ borderTop: '3px solid #22c55e' }}>
          <div className="kpi-label">Supply/Demand Match (avg)</div>
          <div className="kpi-value">
            {_safeNum(stats.ratio_avg != null ? stats.ratio_avg * 100 : null, 1)}
            <span className="kpi-unit">%</span>
          </div>
          <div className="kpi-sub">
            best: {_safeNum(stats.ratio_best != null ? stats.ratio_best * 100 : null, 1)}% · last:{' '}
            {_safeNum(stats.ratio_last != null ? stats.ratio_last * 100 : null, 1)}%
          </div>
        </div>

        <div className="kpi-card" style={{ borderTop: '3px solid #8b5cf6' }}>
          <div className="kpi-label">Days Tracked</div>
          <div className="kpi-value">{stats.n_days}</div>
          <div className="kpi-sub">sim_day(s) of data</div>
        </div>
      </div>

      <div className="chart-card" style={{ marginTop: '1rem' }}>
        <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.95rem' }}>
          Efficiency Trends (last {chartData.length} days)
        </h4>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--grid-stroke, #e2e8f0)" />
            <XAxis dataKey="day" stroke="var(--text-secondary, #64748b)" />
            <YAxis yAxisId="left" stroke="#ef4444" label={{ value: 'SEK/t', angle: -90, position: 'insideLeft' }} />
            <YAxis yAxisId="right" orientation="right" stroke="#f59e0b" label={{ value: 'kg/t', angle: 90, position: 'insideRight' }} />
            <Tooltip
              contentStyle={{
                background: 'var(--tooltip-bg, #fff)',
                border: '1px solid var(--tooltip-border, #cbd5e1)',
                color: 'var(--tooltip-text, #1e293b)',
              }}
            />
            <Legend />
            <Line yAxisId="left" type="monotone" dataKey="cost_per_ton" name="Cost/ton" stroke="#ef4444" strokeWidth={2} dot={false} />
            <Line yAxisId="right" type="monotone" dataKey="co2_per_ton" name="CO₂/ton" stroke="#f59e0b" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}