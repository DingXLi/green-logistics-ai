/**
 * SeasonalComparison - 夏季 vs 冬季 KPI 对比
 *
 * 数据源: GET /api/persistence/seasonal-timeseries
 *
 * 按月份聚合 KPI, 让用户对比不同季节的成本/CO2/吞吐。
 * 帮助理解 "seasonal_factor 是否真的影响业务 KPI"。
 */
import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  LineChart, Line, ComposedChart,
} from 'recharts'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

export function SeasonalComparison() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/persistence/seasonal-timeseries`)
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

  if (loading) return <div className="empty">Loading seasonal KPI…</div>
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data || data.length === 0) {
    return (
      <div className="chart-card">
        <h3>📊 Seasonal KPI Comparison</h3>
        <p className="chart-subtitle">
          Aggregated KPIs by month. Run some cycles first to see seasonal patterns.
        </p>
        <div className="empty">
          No monthly data yet. New cycles will start recording seasonal_factor + month.
        </div>
      </div>
    )
  }

  // 计算 summer (May-Aug, 月 5-8) vs winter (Nov-Feb, 月 11-12 + 1-2) 平均
  const summer = data.filter(d => [5, 6, 7, 8].includes(d.month))
  const winter = data.filter(d => [11, 12, 1, 2].includes(d.month))
  const avgSummerCost = summer.length
    ? summer.reduce((s, m) => s + m.avg_cost_sek_per_cycle, 0) / summer.length
    : 0
  const avgWinterCost = winter.length
    ? winter.reduce((s, m) => s + m.avg_cost_sek_per_cycle, 0) / winter.length
    : 0
  const avgSummerCO2 = summer.length
    ? summer.reduce((s, m) => s + m.avg_co2_per_cycle, 0) / summer.length
    : 0
  const avgWinterCO2 = winter.length
    ? winter.reduce((s, m) => s + m.avg_co2_per_cycle, 0) / winter.length
    : 0

  return (
    <div className="chart-card">
      <h3>📊 Seasonal KPI Comparison</h3>
      <p className="chart-subtitle">
        Aggregated cost + CO₂ by month (per cycle averages).
        Shows whether summer (May–Aug) actually costs more / emits more.
      </p>

      {summer.length > 0 && winter.length > 0 && (
        <div className="seasonal-summary">
          <div className="summary-card">
            <div className="summary-label">☀️ Summer avg cost</div>
            <div className="summary-value">{Math.round(avgSummerCost).toLocaleString()} SEK</div>
          </div>
          <div className="summary-card">
            <div className="summary-label">❄️ Winter avg cost</div>
            <div className="summary-value">{Math.round(avgWinterCost).toLocaleString()} SEK</div>
          </div>
          <div className="summary-card">
            <div className="summary-label">☀️ Summer avg CO₂</div>
            <div className="summary-value">{Math.round(avgSummerCO2).toLocaleString()} kg</div>
          </div>
          <div className="summary-card">
            <div className="summary-label">❄️ Winter avg CO₂</div>
            <div className="summary-value">{Math.round(avgWinterCO2).toLocaleString()} kg</div>
          </div>
        </div>
      )}

      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="month_name" stroke="#64748b" />
          <YAxis yAxisId="left" stroke="#ef4444" />
          <YAxis yAxisId="right" orientation="right" stroke="#22c55e" />
          <Tooltip />
          <Legend />
          <Bar yAxisId="left" dataKey="avg_cost_sek_per_cycle" name="Cost (SEK)" fill="#ef4444" />
          <Line yAxisId="right" type="monotone" dataKey="avg_seasonal_factor" name="Seasonal factor" stroke="#22c55e" strokeWidth={2} />
        </ComposedChart>
      </ResponsiveContainer>

      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="month_name" stroke="#64748b" />
          <YAxis stroke="#64748b" />
          <Tooltip />
          <Legend />
          <Bar dataKey="avg_co2_per_cycle" name="CO₂ (kg)" fill="#f59e0b" />
          <Bar dataKey="total_tons" name="Tons" fill="#3b82f6" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}