/**
 * FleetUtilizationChart - Fleet 利用率时间序列图 (iter #9)
 *
 * 数据源: GET /api/persistence/fleet-timeseries
 *
 * 显示 sim_day 维度的:
 * - 车队利用率 (avg fleet_utilization_pct)
 * - 车辆使用数 vs 可用数 (vehicles_used / vehicles_available)
 * - 总运输距离
 *
 * 用途: 监控车队调度模式, 找高峰/低谷 sim_day。
 */

import { useState, useEffect } from 'react'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

export function FleetUtilizationChart() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/persistence/fleet-timeseries`)
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

  if (loading) return <LoadingSpinner label="Loading fleet trend…" />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data || data.length === 0) {
    return (
      <div className="chart-card">
        <h3>🚚 Fleet Utilization Trend</h3>
        <p className="chart-subtitle">
          Fleet usage, utilization, and distance by sim-day. Run some cycles first.
        </p>
        <div className="empty">No fleet timeseries data — run optimization cycles to populate.</div>
      </div>
    )
  }

  // 格式化数据
  const formatted = data.map(d => ({
    day: `D${d.sim_day}`,
    sim_day: d.sim_day,
    vehicles_used: d.n_vehicles_used || 0,
    vehicles_available: d.n_vehicles_available || 0,
    util: d.fleet_utilization_pct || 0,
    distance_km: d.total_distance_km || 0,
    matches: d.n_matches || 0,
    tons: d.total_tons || 0,
  }))

  // 计算 summary
  const avg = arr => arr.length ? arr.reduce((s, x) => s + x, 0) / arr.length : 0
  const avgUtil = avg(formatted.map(d => d.util))
  const maxUtilDay = formatted.reduce((m, d) => d.util > m.util ? d : m, formatted[0])
  const totalDistance = formatted.reduce((s, d) => s + d.distance_km, 0)
  const totalMatches = formatted.reduce((s, d) => s + d.matches, 0)

  return (
    <div className="chart-card">
      <h3>🚚 Fleet Utilization Trend</h3>
      <p className="chart-subtitle">
        Vehicle usage and utilization over sim-days. Watch for high/low utilization days.
      </p>

      {/* Summary */}
      <div className="fleet-summary-grid">
        <div className="summary-card">
          <div className="summary-label">Avg Utilization</div>
          <div className="summary-value">{avgUtil.toFixed(1)}%</div>
          <div className="summary-sub">across {formatted.length} days</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Peak Day</div>
          <div className="summary-value">{maxUtilDay.util.toFixed(0)}%</div>
          <div className="summary-sub">D{maxUtilDay.sim_day}</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Total Distance</div>
          <div className="summary-value">{totalDistance.toFixed(1)}</div>
          <div className="summary-sub">km across all cycles</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Total Matches</div>
          <div className="summary-value">{totalMatches}</div>
          <div className="summary-sub">supply ↔ demand</div>
        </div>
      </div>

      {/* Bar: vehicles used, Line: utilization */}
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={formatted}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e0e0e0" />
          <XAxis dataKey="day" />
          <YAxis yAxisId="left" label={{ value: 'Vehicles', angle: -90, position: 'insideLeft' }} />
          <YAxis yAxisId="right" orientation="right" domain={[0, 100]}
                 label={{ value: 'Util %', angle: 90, position: 'insideRight' }} />
          <Tooltip />
          <Legend />
          <Bar yAxisId="left" dataKey="vehicles_available" fill="#bae6fd"
               name="Vehicles Available" />
          <Bar yAxisId="left" dataKey="vehicles_used" fill="#0284c7"
               name="Vehicles Used" />
          <Line yAxisId="right" type="monotone" dataKey="util"
                stroke="#dc2626" strokeWidth={2} dot={{ r: 3 }}
                name="Utilization %" />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

export default FleetUtilizationChart