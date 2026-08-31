/**
 * LlmCostTimeseriesChart - LLM usage time series (iter #28)
 *
 * 数据源: GET /api/persistence/llm-cost-timeseries
 *
 * 显示:
 * - Bar chart: LLM 真实调用数 vs fallback 数 (per sim_day)
 * - Line chart: avg_confidence (per sim_day)
 * - 顶部 4 个 KPI: n_days / total_decisions / total_llm / total_fallback
 * - 时间窗口控制: ?llm_cost_since=N&llm_cost_until=M
 *
 * 用途: 监控 LLM 使用趋势, 检测 fallback 频率异常
 */

import { useState, useEffect } from 'react'
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  LineChart,
  Line,
  ComposedChart,
} from 'recharts'

import { LoadingSpinner } from '../common/LoadingSpinner'
import { useUrlState } from '../../hooks/useUrlState'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const REFRESH_INTERVAL_MS = 60000

function KpiCard({ label, value, unit, accent }) {
  return (
    <div className="kpi-card" style={{ borderTop: `3px solid ${accent}` }}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">
        {value}
        <span className="kpi-unit">{unit}</span>
      </div>
    </div>
  )
}

export function LlmCostTimeseriesChart() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // iter #25 URL state
  const [sinceSimDay] = useUrlState('llm_cost_since', 0, 'int')
  const [untilSimDay] = useUrlState('llm_cost_until', 999, 'int')

  const fetchData = async () => {
    try {
      const url = `${API_BASE}/persistence/llm-cost-timeseries?since_sim_day=${sinceSimDay}&until_sim_day=${untilSimDay}`
      const resp = await fetch(url)
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${await resp.text()}`)
      }
      const json = await resp.json()
      setData(json)
      setError(null)
    } catch (e) {
      setError(e.message || 'fetch failed')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, REFRESH_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [sinceSimDay, untilSimDay])

  if (loading) return <LoadingSpinner label="Loading LLM cost timeseries…" />
  if (error) {
    return (
      <div className="chart-card">
        <h3>🤖 LLM Cost Time-series (iter #28)</h3>
        <div className="error-banner">⚠️ {error}</div>
      </div>
    )
  }
  if (!data) return null

  const rows = data.rows || []
  const totalDecisions = rows.reduce((s, r) => s + (r.n_decisions || 0), 0)
  const totalLlm = rows.reduce((s, r) => s + (r.llm_n || 0), 0)
  const totalFallback = rows.reduce((s, r) => s + (r.fallback_n || 0), 0)
  const llmSuccessRate = totalDecisions > 0
    ? Math.round((totalLlm / totalDecisions) * 100 * 100) / 100
    : 0

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <h3>🤖 LLM Cost Time-series (iter #28)</h3>
        <span className="chart-card-sub">
          LLM usage by sim_day · sim_day {sinceSimDay} → {untilSimDay}
        </span>
      </div>

      {/* KPI cards */}
      <div className="kpi-grid">
        <KpiCard label="Sim days" value={rows.length} unit="" accent="#3b82f6" />
        <KpiCard label="Total decisions" value={totalDecisions.toLocaleString()} unit="" accent="#8b5cf6" />
        <KpiCard label="LLM calls" value={totalLlm.toLocaleString()} unit="" accent="#22c55e" />
        <KpiCard label="Fallback" value={totalFallback.toLocaleString()} unit="" accent="#f59e0b" />
      </div>

      {totalDecisions === 0 ? (
        <div className="empty-state">
          🤖 No LLM decisions in the current window. Run some cycles to populate.
        </div>
      ) : (
        <>
          {/* Bar chart: LLM vs fallback per sim_day */}
          <div className="chart-row" style={{ height: 260, marginTop: '1rem' }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={rows}
                margin={{ top: 5, right: 30, left: 60, bottom: 5 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#475569" />
                <XAxis dataKey="sim_day" stroke="#94a3b8" />
                <YAxis stroke="#94a3b8" />
                <Tooltip
                  contentStyle={{ background: '#1e293b', border: '1px solid #475569' }}
                />
                <Legend />
                <Bar dataKey="llm_n" name="LLM (Gemini)" stackId="a" fill="#22c55e" />
                <Bar dataKey="fallback_n" name="Fallback (heuristic)" stackId="a" fill="#f59e0b" />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Stats row: avg multiplier + avg confidence per sim_day */}
          <div className="chart-row" style={{ height: 220, marginTop: '1rem' }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={rows}
                margin={{ top: 5, right: 30, left: 60, bottom: 5 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#475569" />
                <XAxis dataKey="sim_day" stroke="#94a3b8" />
                <YAxis stroke="#94a3b8" domain={[0, 1.5]} />
                <Tooltip
                  contentStyle={{ background: '#1e293b', border: '1px solid #475569' }}
                />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="avg_multiplier"
                  name="Avg multiplier"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                />
                <Line
                  type="monotone"
                  dataKey="avg_confidence"
                  name="Avg confidence"
                  stroke="#8b5cf6"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Summary banner */}
          <div className="info-banner" style={{ marginTop: '1rem' }}>
            ℹ️ LLM success rate: <strong>{llmSuccessRate}%</strong> · Total decisions: <strong>{totalDecisions}</strong> · Window: <strong>{rows.length}</strong> sim_days
          </div>
        </>
      )}
    </div>
  )
}

export default LlmCostTimeseriesChart
