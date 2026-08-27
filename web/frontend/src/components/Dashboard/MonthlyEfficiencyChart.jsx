/**
 * MonthlyEfficiencyChart - 月度 efficiency 趋势图 (iter #9)
 *
 * 数据源: GET /api/persistence/monthly-efficiency-trend
 *
 * 显示 1-12 月的 cost per ton / CO2 per ton 趋势,
 * 高亮 summer (Jun-Aug) 和 winter (Dec-Feb) 季节对比。
 *
 * 用途: 验证 seasonal_factor 是否真的影响 KPI (夏季 supply 上升 → cost/ton 应下降)。
 */

import { useState, useEffect } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  ComposedChart, Bar, ReferenceArea,
} from 'recharts'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

// 季节区域 (用于背景 highlight)
const SEASON_REGIONS = [
  { start: 0, end: 1, label: 'Winter', color: 'rgba(186, 230, 253, 0.15)' },  // Dec, Jan
  { start: 5, end: 7, label: 'Summer', color: 'rgba(254, 240, 138, 0.15)' },   // Jun-Aug
]

export function MonthlyEfficiencyChart() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/persistence/monthly-efficiency-trend`)
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

  if (loading) return <LoadingSpinner label="Loading monthly efficiency…" />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data || data.length === 0) {
    return (
      <div className="chart-card">
        <h3>📅 Monthly Efficiency Trend</h3>
        <p className="chart-subtitle">
          Cost per ton / CO₂ per ton aggregated by month. Run some cycles first
          to see monthly patterns.
        </p>
        <div className="empty">No monthly data — run optimization cycles to populate.</div>
      </div>
    )
  }

  // 12 月填充 (缺失月份用 0 占位, 让 x 轴连续)
  const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  const full = monthNames.map((m, idx) => {
    const found = data.find(d => (d.seasonal_month || d.month) === idx + 1)
    return {
      month: m,
      monthIdx: idx + 1,
      cost_per_ton: found?.cost_per_ton_sek ?? null,
      co2_per_ton: found?.co2_per_ton_kg ?? null,
      n_cycles: found?.n_cycles ?? 0,
      seasonal_factor: found?.avg_seasonal_factor ?? null,
      util: found?.avg_fleet_util_pct ?? null,
    }
  })

  // 计算 summer vs winter 平均
  const summerMonths = full.filter(m => [6, 7, 8].includes(m.monthIdx))
  const winterMonths = full.filter(m => [12, 1, 2].includes(m.monthIdx))
  const avg = (arr, key) => {
    const valid = arr.filter(x => x[key] != null)
    return valid.length ? valid.reduce((s, x) => s + x[key], 0) / valid.length : null
  }
  const summerCost = avg(summerMonths, 'cost_per_ton')
  const winterCost = avg(winterMonths, 'cost_per_ton')
  const summerCo2 = avg(summerMonths, 'co2_per_ton')
  const winterCo2 = avg(winterMonths, 'co2_per_ton')

  return (
    <div className="chart-card">
      <h3>📅 Monthly Efficiency Trend</h3>
      <p className="chart-subtitle">
        Cost per ton / CO₂ per ton by month. Highlights: summer (Jun-Aug, yellow) vs winter (Dec-Jan, blue).
      </p>

      {/* Summary 卡片 */}
      <div className="seasonal-summary-grid">
        <div className="summary-card summer">
          <div className="summary-label">🌞 Summer Avg</div>
          <div className="summary-value">
            {summerCost != null ? `${summerCost.toFixed(1)} SEK/t` : '—'}
          </div>
          <div className="summary-sub">
            {summerCo2 != null ? `${summerCo2.toFixed(2)} kgCO₂/t` : '—'}
          </div>
        </div>
        <div className="summary-card winter">
          <div className="summary-label">❄️ Winter Avg</div>
          <div className="summary-value">
            {winterCost != null ? `${winterCost.toFixed(1)} SEK/t` : '—'}
          </div>
          <div className="summary-sub">
            {winterCo2 != null ? `${winterCo2.toFixed(2)} kgCO₂/t` : '—'}
          </div>
        </div>
        {summerCost != null && winterCost != null && winterCost > 0 && (
          <div className="summary-card delta">
            <div className="summary-label">Δ (Summer − Winter)</div>
            <div className="summary-value">
              {((summerCost - winterCost) / winterCost * 100).toFixed(1)}%
            </div>
            <div className="summary-sub">
              {(summerCost - winterCost).toFixed(1)} SEK/t
            </div>
          </div>
        )}
      </div>

      {/* Line chart: cost/CO2 per ton by month */}
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={full}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e0e0e0" />
          <XAxis dataKey="month" />
          <YAxis yAxisId="left" label={{ value: 'SEK/ton', angle: -90, position: 'insideLeft' }} />
          <YAxis yAxisId="right" orientation="right" label={{ value: 'kgCO₂/ton', angle: 90, position: 'insideRight' }} />
          <Tooltip />
          <Legend />
          {/* 季节高亮区 */}
          {SEASON_REGIONS.map((r, i) => (
            <ReferenceArea
              key={i}
              yAxisId="left"
              x1={monthNames[r.start]}
              x2={monthNames[r.end]}
              fill={r.color}
              stroke="none"
              label={{ value: r.label, position: 'insideTop', fontSize: 10, fill: '#666' }}
            />
          ))}
          <Line
            yAxisId="left"
            type="monotone"
            dataKey="cost_per_ton"
            stroke="#2e7d32"
            strokeWidth={2}
            dot={{ r: 4 }}
            name="Cost (SEK/ton)"
            connectNulls
          />
          <Line
            yAxisId="right"
            type="monotone"
            dataKey="co2_per_ton"
            stroke="#dc2626"
            strokeWidth={2}
            dot={{ r: 4 }}
            name="CO₂ (kg/ton)"
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>

      {/* 小注脚 */}
      <div className="chart-footer">
        Total cycles: {data.reduce((s, d) => s + (d.n_cycles || 0), 0)}
        {' · '}
        Months with data: {data.length}/12
      </div>
    </div>
  )
}

export default MonthlyEfficiencyChart