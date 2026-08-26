/**
 * Dashboard - KPI 时间序列 + Pareto 前沿 + 仿真控制
 *
 * 数据源:
 *   GET /api/persistence/kpi-timeseries -> [{sim_day, tons, cost_sek, co2_kg, util_pct, matches}, ...]
 *   GET /api/persistence/summary       -> {n_cycles, total_tons, total_cost_sek, total_co2_kg, avg_utilization, llm_decisions}
 *   GET /api/optimize/pareto           -> {pareto: [...]} 或类似
 *   POST /api/optimize                 -> 触发一次单 cycle 优化
 *
 * 展示:
 *   - 4 个 KPI 卡片（total cost / total CO2 / avg utilization / matches）
 *   - Recharts 多线图（cost / CO2 / utilization 30 天趋势）
 *   - Recharts 柱状图（每天 matches 数量）
 *   - Pareto 前沿散点图（cost vs CO2 tradeoff）
 *   - 仿真控制（开始新一轮 / 自动刷新）
 */
import { useState, useEffect, useCallback } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  ScatterChart, Scatter, BarChart, Bar, ZAxis
} from 'recharts'
import { useWebSocket } from '../../hooks/useWebSocket'
import { LiveCycleIndicator } from './LiveCycleIndicator'
import { SeasonalHeatmap } from './SeasonalHeatmap'
import { SeasonalComparison } from './SeasonalComparison'
import { CarbonScenarios } from './CarbonScenarios'
import { FacilitiesList } from './FacilitiesList'

// API base: Vite env var > localhost fallback
const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

// 颜色 (跟 App.css 一致)
const COLORS = {
  cost: '#ef4444',      // 红
  co2: '#f59e0b',       // 橙
  util: '#22c55e',      // 绿
  matches: '#3b82f6',   // 蓝
  pareto: '#8b5cf6',    // 紫
}

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

function KPISummary({ data }) {
  if (!data) return null
  const { total_tons, total_cost_sek, total_co2_kg, avg_utilization, n_cycles, llm_decisions } = data
  return (
    <div className="kpi-grid">
      <KpiCard label="Total Cost" value={Math.round(total_cost_sek || 0).toLocaleString()} unit=" SEK" accent={COLORS.cost} />
      <KpiCard label="Total CO₂" value={Math.round(total_co2_kg || 0).toLocaleString()} unit=" kg" accent={COLORS.co2} />
      <KpiCard label="Avg Utilization" value={(avg_utilization || 0).toFixed(1)} unit="%" accent={COLORS.util} />
      <KpiCard label="Transported" value={Math.round(total_tons || 0).toLocaleString()} unit=" t" accent={COLORS.matches} />
      <KpiCard label="Sim Days" value={n_cycles || 0} unit=" days" accent="#64748b" />
      <KpiCard
        label="LLM Decisions"
        value={llm_decisions?.n_real_llm || 0}
        unit={` / ${llm_decisions?.n_total || 0}`}
        accent="#8b5cf6"
      />
    </div>
  )
}

function KPITimeseries({ data }) {
  if (!data || data.length === 0) {
    return <div className="empty">No time-series data yet. Run simulation first.</div>
  }
  // 把 sim_day 格式化
  const formatted = data.map(d => ({
    day: `D${d.sim_day}`,
    cost: Math.round(d.cost_sek || 0),
    co2: Math.round(d.co2_kg || 0),
    util: Number((d.util_pct || 0).toFixed(1)),
    matches: d.matches || 0,
  }))

  return (
    <div className="chart-row">
      <div className="chart-card">
        <h3>📈 30-Day KPI Trends</h3>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={formatted} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="day" stroke="#64748b" />
            <YAxis yAxisId="left" stroke={COLORS.cost} />
            <YAxis yAxisId="right" orientation="right" stroke={COLORS.util} />
            <Tooltip />
            <Legend />
            <Line yAxisId="left" type="monotone" dataKey="cost" name="Cost (SEK)" stroke={COLORS.cost} dot={false} strokeWidth={2} />
            <Line yAxisId="left" type="monotone" dataKey="co2" name="CO₂ (kg)" stroke={COLORS.co2} dot={false} strokeWidth={2} />
            <Line yAxisId="right" type="monotone" dataKey="util" name="Utilization (%)" stroke={COLORS.util} dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="chart-card">
        <h3>🚚 Matches per Day</h3>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={formatted} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="day" stroke="#64748b" />
            <YAxis stroke="#64748b" />
            <Tooltip />
            <Bar dataKey="matches" fill={COLORS.matches} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function ParetoChart({ data }) {
  if (!data || data.length === 0) {
    return <div className="empty">No Pareto data available.</div>
  }
  // 适配后端字段: {cost_weight, co2_weight, cost_sek, co2_kg, total_objective}
  // 映射到 Recharts 期望的 {cost, co2, alpha}
  const points = data.map(p => ({
    cost: Math.round(p.cost_sek || 0),
    co2: Math.round(p.co2_kg || 0),
    alpha: p.cost_weight ?? 0.5,
    objective: p.total_objective,
  }))
  return (
    <div className="chart-card">
      <h3>⚖️ Pareto Frontier — Cost vs CO₂</h3>
      <p className="chart-subtitle">Each point is a different α weighting (cost vs emissions)</p>
      <ResponsiveContainer width="100%" height={320}>
        <ScatterChart margin={{ top: 10, right: 20, left: 20, bottom: 20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis type="number" dataKey="cost" name="Cost (SEK)" stroke="#64748b" />
          <YAxis type="number" dataKey="co2" name="CO₂ (kg)" stroke="#64748b" />
          <ZAxis type="number" dataKey="alpha" range={[60, 400]} name="α" />
          <Tooltip cursor={{ strokeDasharray: '3 3' }} />
          <Scatter name="Pareto" data={points} fill={COLORS.pareto} />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}

function SimulationControl({ onRun, lastResult, running }) {
  return (
    <div className="control-panel">
      <button
        className="optimize-btn"
        onClick={onRun}
        disabled={running}
      >
        {running ? '⏳ Running...' : '🚀 Run Next Cycle'}
      </button>
      {lastResult && (
        <div className="control-result">
          <span className="result-tag">Last run:</span>{' '}
          {lastResult.matches_count} matches ·{' '}
          {lastResult.total_tons?.toFixed(1)} t ·{' '}
          {Math.round(lastResult.total_cost_sek)} SEK ·{' '}
          {Math.round(lastResult.total_co2_kg)} kg CO₂
        </div>
      )}
    </div>
  )
}

export default function Dashboard() {
  const [summary, setSummary] = useState(null)
  const [timeseries, setTimeseries] = useState([])
  const [pareto, setPareto] = useState([])
  const [lastRun, setLastRun] = useState(null)
  const [running, setRunning] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('overview')  // 'overview' | 'scenarios' | 'network'

  const fetchAll = useCallback(async () => {
    try {
      const [sumRes, tsRes, paretoRes] = await Promise.all([
        fetch(`${API_BASE}/persistence/summary`),
        fetch(`${API_BASE}/persistence/kpi-timeseries`),
        fetch(`${API_BASE}/optimize/pareto`).catch(() => null),
      ])
      const sum = await sumRes.json()
      const ts = await tsRes.json()
      let pa = []
      if (paretoRes && paretoRes.ok) {
        const paData = await paretoRes.json()
        pa = paData.pareto || paData || []
      }
      setSummary(sum)
      setTimeseries(ts)
      setPareto(pa)
      setError(null)
    } catch (e) {
      console.error('Dashboard fetch failed:', e)
      setError(e.message)
    }
  }, [])

  // WebSocket: 实时接收 cycle_update → 主动刷新数据
  const handleWsMessage = useCallback((msg) => {
    if (msg?.type === 'cycle_update') {
      // 后端告知有新的 cycle 完成, 主动重新拉数据
      fetchAll()
    }
  }, [fetchAll])
  const { lastMessage: wsMessage, connected: wsConnected } = useWebSocket(
    '/ws/cycle-updates',
    { onMessage: handleWsMessage }
  )

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(fetchAll, 10000) // 10s 自动刷新
    return () => clearInterval(id)
  }, [autoRefresh, fetchAll])

  const runCycle = async () => {
    setRunning(true)
    try {
      const res = await fetch(`${API_BASE}/optimize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_simulation: false }),
      })
      const data = await res.json()
      setLastRun(data)
      await fetchAll()
    } catch (e) {
      setError(e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <h2>📊 30-Day Simulation Dashboard</h2>
        <label className="auto-refresh">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={e => setAutoRefresh(e.target.checked)}
          />
          Auto-refresh (10s)
        </label>
      </div>

      {error && <div className="error-banner">⚠️ {error}</div>}

      <LiveCycleIndicator message={wsMessage} connected={wsConnected} />

      <KPISummary data={summary} />

      {/* Tab 切换器 */}
      <div className="dashboard-tabs">
        <button
          className={`tab-btn ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          📈 Overview & Pareto
        </button>
        <button
          className={`tab-btn ${activeTab === 'scenarios' ? 'active' : ''}`}
          onClick={() => setActiveTab('scenarios')}
        >
          🌦️ Seasonal & Carbon
        </button>
        <button
          className={`tab-btn ${activeTab === 'network' ? 'active' : ''}`}
          onClick={() => setActiveTab('network')}
        >
          🏭 Network
        </button>
      </div>

      {/* Tab 内容 */}
      {activeTab === 'overview' && (
        <>
          <KPITimeseries data={timeseries} />

          <div className="chart-row">
            <ParetoChart data={pareto} />
            <div className="chart-card control-card">
              <h3>🎮 Simulation Control</h3>
              <SimulationControl onRun={runCycle} lastResult={lastRun} running={running} />
              <div className="info-list">
                <div className="info-item">
                  <span className="info-label">Data source:</span>{' '}
                  <code>data/month_simulation.db</code>
                </div>
                <div className="info-item">
                  <span className="info-label">Backend:</span>{' '}
                  <code>http://localhost:8000/docs</code>
                </div>
                <div className="info-item">
                  <span className="info-label">Refresh interval:</span> 10s
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {activeTab === 'scenarios' && (
        <>
          <SeasonalHeatmap currentMonth={
            // 从 WS 推送的最新 cycle 拿到 sim_day
            wsMessage?.type === 'cycle_update' && wsMessage?.data?.sim_day
              ? (Math.floor((wsMessage.data.sim_day - 1) / 30) % 12 + 1)
              : null
          } />

          <SeasonalComparison />

          <CarbonScenarios />
        </>
      )}

      {activeTab === 'network' && (
        <FacilitiesList />
      )}
    </div>
  )
}
