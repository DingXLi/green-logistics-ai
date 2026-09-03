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
import { useState, useEffect, useCallback, lazy, Suspense } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  ScatterChart, Scatter, BarChart, Bar, ZAxis
} from 'recharts'
import { useWebSocket } from '../../hooks/useWebSocket'
import { useSharedWebSocket } from '../../hooks/useSharedWebSocket'
import { useDashboardSummary } from '../../hooks/useDashboardSummary'
import { useUrlState } from '../../hooks/useUrlState'
import { LiveCycleIndicator } from './LiveCycleIndicator'

// iter #5: code-splitting — lazy load 重型 tab 组件
// SeasonalHeatmap / SeasonalComparison / CarbonScenarios / FacilitiesList 都是
// recharts-heavy 或有大量 fetch logic, 改成 lazy + Suspense 拆分成独立 chunk
const SeasonalHeatmap = lazy(() => import('./SeasonalHeatmap').then(m => ({ default: m.SeasonalHeatmap })))
const SeasonalComparison = lazy(() => import('./SeasonalComparison').then(m => ({ default: m.SeasonalComparison })))
const CarbonScenarios = lazy(() => import('./CarbonScenarios').then(m => ({ default: m.CarbonScenarios })))
const FacilitiesList = lazy(() => import('./FacilitiesList').then(m => ({ default: m.FacilitiesList })))
const MonthlyEfficiencyChart = lazy(() => import('./MonthlyEfficiencyChart').then(m => ({ default: m.MonthlyEfficiencyChart })))
const FleetUtilizationChart = lazy(() => import('./FleetUtilizationChart').then(m => ({ default: m.FleetUtilizationChart })))
const MaterialsOverview = lazy(() => import('./MaterialsOverview').then(m => ({ default: m.MaterialsOverview })))
// iter #16: material aggregates table + cycle KPI summary
const MaterialAggregates = lazy(() => import('./MaterialAggregates').then(m => ({ default: m.MaterialAggregates })))
const CycleKpiSummary = lazy(() => import('./CycleKpiSummary').then(m => ({ default: m.CycleKpiSummary })))
// iter #17: DB stats badge (size + table rows + indexes)
const DbStatsBadge = lazy(() => import('./DbStatsBadge').then(m => ({ default: m.DbStatsBadge })))
// iter #20: cohort retention by period (early vs late trend)
const CohortRetentionByPeriod = lazy(() => import('./CohortRetentionByPeriod').then(m => ({ default: m.CohortRetentionByPeriod })))
// iter #22: API performance monitoring (perf middleware backend)
const PerfStats = lazy(() => import('./PerfStats').then(m => ({ default: m.PerfStats })))
// iter #22: LLM token usage + cost tracking
const LLMStats = lazy(() => import('./LLMStats').then(m => ({ default: m.LLMStats })))
// iter #23: DB export menu (CSV / JSON / NDJSON / Parquet)
const ExportButton = lazy(() => import('./ExportButton').then(m => ({ default: m.ExportButton })))
// iter #26: KPI forecast (linear regression on history)
const ForecastPanel = lazy(() => import('./ForecastPanel').then(m => ({ default: m.ForecastPanel })))
// iter #30: ensemble forecast confidence panel
const ForecastConfidencePanel = lazy(() => import('./ForecastConfidencePanel').then(m => ({ default: m.ForecastConfidencePanel })))
// iter #36: persisted best_method preferences per metric (auto-resolved by /forecast?method=auto)
const ForecastMethodPrefs = lazy(() => import('./ForecastMethodPrefs').then(m => ({ default: m.ForecastMethodPrefs })))
// iter #37: seasonal perturbation panel (admin shocks overlay baseline factors)
const SeasonalPerturbationPanel = lazy(() => import('./SeasonalPerturbationPanel').then(m => ({ default: m.SeasonalPerturbationPanel })))
// iter #38: perturbation impact analytics (base vs effective seasonal factor)
const PerturbationImpactPanel = lazy(() => import('./PerturbationImpactPanel').then(m => ({ default: m.PerturbationImpactPanel })))
// iter #40: on-demand simulation runner (POST /api/simulate/run)
const SimulationControlPanel = lazy(() => import('./SimulationControlPanel').then(m => ({ default: m.SimulationControlPanel })))
// iter #28: LLM cost time-series chart
const LlmCostTimeseriesChart = lazy(() => import('./LlmCostTimeseriesChart').then(m => ({ default: m.LlmCostTimeseriesChart })))
// iter #29: LLM usage/cost forecast panel
const LlmCostForecastPanel = lazy(() => import('./LlmCostForecastPanel').then(m => ({ default: m.LlmCostForecastPanel })))
// iter #11: cycle history with expandable detail
const CycleHistory = lazy(() => import('./CycleHistory').then(m => ({ default: m.CycleHistory })))
// iter #41: Pareto-frontier sweet-spot finder (auto-recommend carbon tax)
const SweetSpot = lazy(() => import('./SweetSpot').then(m => ({ default: m.SweetSpot })))
// iter #41: per-vehicle historical stats (efficiency / utilization drill-down)
const VehicleStats = lazy(() => import('./VehicleStats').then(m => ({ default: m.VehicleStats })))
// iter #42: forecast calibration (predicted vs actual accuracy)
const ForecastCalibration = lazy(() => import('./ForecastCalibration').then(m => ({ default: m.ForecastCalibration })))

// iter #7: 通用 LoadingSpinner for fetch + Suspense fallback
import { LoadingSpinner } from '../common/LoadingSpinner'
import { WSStatusIndicator } from '../common/WSStatusIndicator'

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
    return (
      <div className="empty">
        <LoadingSpinner size="md" label="No time-series data yet. Run simulation first." />
      </div>
    )
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
    return (
      <div className="empty">
        <LoadingSpinner size="md" label="No Pareto data available." />
      </div>
    )
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

function SchedulerControl() {
  // iter #10: 手动控制后台 scheduler (start / stop / restart)
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/scheduler/status`)
      const data = await r.json()
      setStatus(data)
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    const id = setInterval(fetchStatus, 15000)  // 15s poll
    return () => clearInterval(id)
  }, [fetchStatus])

  const control = async (action) => {
    setLoading(true)
    try {
      const r = await fetch(`${API_BASE}/scheduler/control?action=${action}`, { method: 'POST' })
      const data = await r.json()
      if (data.success) {
        await fetchStatus()
      } else {
        setError(`Action ${action} failed`)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  if (error) return <div className="error-banner small">⚠️ Scheduler: {error}</div>
  if (!status) return <LoadingSpinner size="sm" label="Loading scheduler…" />

  const active = status.active
  const reason = status.reason

  return (
    <div className="scheduler-control-panel">
      <div className="scheduler-status">
        <span className={`scheduler-dot ${active ? 'active' : 'inactive'}`} />
        <span className="scheduler-label">
          Scheduler: <strong>{active ? 'Running' : 'Stopped'}</strong>
        </span>
        {status.cycle_count != null && (
          <span className="scheduler-cycles">· {status.cycle_count} cycles</span>
        )}
      </div>
      {reason && <div className="scheduler-reason">⚠️ {reason}</div>}
      <div className="scheduler-buttons">
        {!active ? (
          <button
            className="scheduler-btn start"
            onClick={() => control('start')}
            disabled={loading || !!reason}
            title={reason || 'Start background scheduler'}
          >
            ▶ Start
          </button>
        ) : (
          <button
            className="scheduler-btn stop"
            onClick={() => control('stop')}
            disabled={loading}
            title="Stop background scheduler"
          >
            ⏸ Stop
          </button>
        )}
        <button
          className="scheduler-btn restart"
          onClick={() => control('restart')}
          disabled={loading || !!reason}
          title="Restart scheduler (stop + start)"
        >
          🔄 Restart
        </button>
      </div>
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
  // iter #7: 记录最近一次 /optimize/pareto 响应的 distance_source, 顶部提示
  const [paretoMeta, setParetoMeta] = useState({ distance_source: null, use_real_roads: true })
  // iter #8: 用户可控制 use_real_roads (toggle) — 重置后 refetch pareto
  const [useRealRoads, setUseRealRoads] = useState(true)
  // iter #7: 从 WS 推送拿 efficiency summary (cost/CO2 per ton)
  const [wsEfficiency, setWsEfficiency] = useState(null)
  // iter #29: 从 WS 推送拿 LLM usage/cost summary
  const [wsLlm, setWsLlm] = useState(null)
  // iter #8: 从 WS 推送拿 fleet metrics (util, vehicles, distance)
  const [wsFleet, setWsFleet] = useState(null)
  // iter #25: URL query param ?tab=overview (deep linkable, shareable)
  // Backward-compat: still read URL hash for old links (e.g., #network)
  const VALID_TABS = ['overview', 'scenarios', 'network', 'performance', 'history']
  const [activeTab, setActiveTabRaw] = useUrlState('tab', 'overview')
  const setActiveTab = setActiveTabRaw  // alias

  // Validate activeTab is in known set
  useEffect(() => {
    if (!VALID_TABS.includes(activeTab)) {
      // fallback to overview if invalid value in URL
      setActiveTabRaw('overview')
    }
  }, [activeTab, setActiveTabRaw])

  // iter #25: also accept legacy hash format (#network) → migrate to ?tab=network
  useEffect(() => {
    if (typeof window === 'undefined') return
    const hash = window.location.hash.replace('#', '')
    if (VALID_TABS.includes(hash)) {
      // Migrate old hash format to query param
      window.history.replaceState({}, '', `?tab=${hash}${window.location.hash}`)
      setActiveTabRaw(hash)
    }
    // Listen for hashchange (legacy support)
    const onHashChange = () => {
      const newHash = window.location.hash.replace('#', '')
      if (VALID_TABS.includes(newHash)) {
        setActiveTabRaw(newHash)
      }
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [setActiveTabRaw])

  // iter #12: useDashboardSummary hook 一次性拿聚合数据 (替代独立 fetch /persistence/summary)
  const { data: summaryBundle, refetch: refetchSummary } = useDashboardSummary({
    autoRefresh: true,
    pollIntervalMs: 10000,
  })

  const fetchAll = useCallback(async () => {
    try {
      // iter #12: 并行 fetch (kpi-timeseries + pareto + LLM stats)
      const [tsRes, paretoRes, llmRes] = await Promise.all([
        fetch(`${API_BASE}/persistence/kpi-timeseries`),
        fetch(`${API_BASE}/optimize/pareto`).catch(() => null),
        fetch(`${API_BASE}/admin/llm-stats?recent=1`).catch(() => null),
      ])
      const ts = await tsRes.json()
      let pa = []
      if (paretoRes && paretoRes.ok) {
        const paData = await paretoRes.json()
        pa = paData.pareto || paData || []
        setParetoMeta({
          distance_source: paData.distance_source || 'unknown',
          use_real_roads: paData.use_real_roads !== false,
        })
      }
      if (llmRes && llmRes.ok) {
        const llmData = await llmRes.json()
        setWsLlm({
          total_calls: llmData.total_calls || 0,
          total_errors: llmData.total_errors || 0,
          total_tokens: llmData.total_tokens || 0,
          total_cost_usd: llmData.total_cost_usd || 0,
          error_rate_pct: llmData.error_rate_pct || 0,
        })
      }
      setTimeseries(ts)
      setPareto(pa)
      setError(null)
    } catch (e) {
      console.error('Dashboard fetch failed:', e)
      setError(e.message)
    }
  }, [useRealRoads])

  // iter #12: aggregator → 喂给 summary state (与后端 get_summary 兼容)
  useEffect(() => {
    if (summaryBundle?.summary && !summaryBundle.summary.error) {
      setSummary(summaryBundle.summary)
    }
  }, [summaryBundle])

  // iter #12: aggregator → 同步 wsEfficiency / wsFleet (后端不会重复 WS 推送这些)
  useEffect(() => {
    if (summaryBundle?.efficiency && !summaryBundle.efficiency.error) {
      setWsEfficiency(summaryBundle.efficiency)
    }
    if (summaryBundle?.last_cycle) {
      setWsFleet(summaryBundle.last_cycle)
    }
  }, [summaryBundle])

  // iter #12: 当 WS 推送 cycle_update 时, 同时 refetch aggregator
  // (复用 refetchSummary 把最新数据 pull 到 hook state)

  // WebSocket: 实时接收 cycle_update → 主动刷新数据
  const handleWsMessage = useCallback((msg) => {
    if (msg?.type === 'cycle_update') {
      // 后端告知有新的 cycle 完成, 主动重新拉数据
      fetchAll()
      refetchSummary()  // iter #12: also refetch aggregator
      // iter #7: 直接用 WS 推送的 efficiency summary, 不需要额外 fetch
      if (msg.data?.efficiency) {
        setWsEfficiency(msg.data.efficiency)
      }
      // iter #29: WS 推送 LLM usage/cost
      if (msg.data?.llm) {
        setWsLlm(msg.data.llm)
      }
      // iter #8: WS 推送的 fleet metrics
      if (msg.data?.fleet) {
        setWsFleet(msg.data.fleet)
      }
    }
  }, [fetchAll, refetchSummary])
  const {
    lastMessage: wsMessage,
    connected: wsConnected,
    reconnecting: wsReconnecting,
    reconnectAttempts: wsAttempts,
    lastError: wsLastError,
    isLeader: wsIsLeader,
  } = useSharedWebSocket(
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
        <div className="header-meta">
          {/* iter #7: 展示最近一次优化使用的路网 source */}
          {paretoMeta.distance_source && (
            <span
              className={`distance-source-badge ${paretoMeta.distance_source}`}
              title={`VRP uses ${paretoMeta.use_real_roads ? 'real OSM roads (fallback to Haversine on error)' : 'Haversine only'}`}
            >
              🛣️ {paretoMeta.distance_source === 'osm' ? 'OSM' : paretoMeta.distance_source === 'haversine' ? 'Haversine' : paretoMeta.distance_source}
            </span>
          )}
          {/* iter #7: WS 推送的运行 efficiency (cost/CO2 per ton) */}
          {wsEfficiency && wsEfficiency.n_cycles > 0 && (
            <span className="ws-efficiency-badge" title="Live efficiency from WebSocket (auto-updated)">
              📊 {wsEfficiency.cost_per_ton_sek != null ? `${wsEfficiency.cost_per_ton_sek.toFixed(1)} SEK/t` : '—'}
              {' · '}
              {wsEfficiency.co2_per_ton_kg != null ? `${wsEfficiency.co2_per_ton_kg.toFixed(2)} kgCO₂/t` : '—'}
            </span>
          )}
          {/* iter #29: WS 推送 LLM usage/cost */}
          {wsLlm && wsLlm.total_calls > 0 && (
            <span className="ws-llm-badge" title="Live LLM usage/cost from WebSocket">
              🤖 {wsLlm.total_calls} calls · ${(wsLlm.total_cost_usd || 0).toFixed(4)} · {wsLlm.total_tokens.toLocaleString()} tok
            </span>
          )}
          {/* iter #8: WS 推送的 fleet metrics */}
          {wsFleet && wsFleet.total_vehicles > 0 && (
            <span className="ws-fleet-badge" title={`Fleet from WebSocket — ${wsFleet.utilization_rate.toFixed(0)}% utilized`}>
              🚚 {wsFleet.total_vehicles} veh · {wsFleet.utilization_rate.toFixed(0)}% util
            </span>
          )}
          {/* iter #8: 用户可控制 use_real_roads (toggle) */}
          <label className="roads-toggle" title="Use OSM real road distances (off = Haversine only, faster but less accurate)">
            <input
              type="checkbox"
              checked={useRealRoads}
              onChange={e => setUseRealRoads(e.target.checked)}
            />
            🛣️ Real Roads
          </label>
          <label className="auto-refresh">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={e => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh (10s)
          </label>
        </div>
      </div>

      {error && <div className="error-banner">⚠️ {error}</div>}

      <LiveCycleIndicator message={wsMessage} connected={wsConnected} />
      <div className="ws-status-row">
        <WSStatusIndicator
          connected={wsConnected}
          reconnecting={wsReconnecting}
          attempts={wsAttempts}
          lastError={wsLastError}
          isLeader={wsIsLeader}
        />
      </div>

      <KPISummary data={summary} />

      {/* iter #17: DB stats badge (size + tables + indexes) */}
      <Suspense fallback={<LoadingSpinner label="Loading DB stats…" />}>
        <DbStatsBadge />
      </Suspense>

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
        {/* iter #22: new Performance tab (perf + LLM) */}
        <button
          className={`tab-btn ${activeTab === 'performance' ? 'active' : ''}`}
          onClick={() => setActiveTab('performance')}
        >
          ⚡ Performance
        </button>
        <button
          className={`tab-btn ${activeTab === 'history' ? 'active' : ''}`}
          onClick={() => setActiveTab('history')}
        >
          📜 History
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
              {/* iter #10: Scheduler 控制 (start/stop/restart) */}
              <SchedulerControl />
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

          {/* iter #9: Monthly efficiency trend chart */}
          <Suspense fallback={<LoadingSpinner label="Loading monthly chart…" />}>
            <MonthlyEfficiencyChart />
          </Suspense>

          {/* iter #9: Fleet utilization trend chart */}
          <Suspense fallback={<LoadingSpinner label="Loading fleet chart…" />}>
            <FleetUtilizationChart />
          </Suspense>

          {/* iter #41: Per-vehicle historical stats */}
          <Suspense fallback={<LoadingSpinner label="Loading vehicle stats…" />}>
            <VehicleStats />
          </Suspense>

          {/* iter #42: Forecast calibration (predicted vs actual) */}
          <Suspense fallback={<LoadingSpinner label="Loading forecast calibration…" />}>
            <ForecastCalibration />
          </Suspense>

          {/* iter #10: Materials overview grid */}
          <Suspense fallback={<LoadingSpinner label="Loading materials…" />}>
            <MaterialsOverview />
          </Suspense>

          {/* iter #16: Cycle KPI summary */}
          <Suspense fallback={<LoadingSpinner label="Loading cycle summary…" />}>
            <CycleKpiSummary />
          </Suspense>

          {/* iter #16: Material aggregates table */}
          <Suspense fallback={<LoadingSpinner label="Loading material aggregates…" />}>
            <MaterialAggregates />
          </Suspense>

          {/* iter #20: Cohort retention by period (trend) */}
          <Suspense fallback={<LoadingSpinner label="Loading cohort retention…" />}>
            <CohortRetentionByPeriod />
          </Suspense>
          {/* iter #26: KPI forecast (next N days prediction) */}
          <Suspense fallback={<LoadingSpinner label="Loading forecast…" />}>
            <ForecastPanel />
            <ForecastMethodPrefs />
            <ForecastConfidencePanel />
            <SeasonalPerturbationPanel />
            <PerturbationImpactPanel />
            <SimulationControlPanel />
          </Suspense>
        </>
      )}

      {activeTab === 'scenarios' && (
        <Suspense fallback={<LoadingSpinner label="Loading scenarios…" />}>
          <SeasonalHeatmap currentMonth={
            // 从 WS 推送的最新 cycle 拿到 sim_day
            wsMessage?.type === 'cycle_update' && wsMessage?.data?.sim_day
              ? (Math.floor((wsMessage.data.sim_day - 1) / 30) % 12 + 1)
              : null
          } />

          <SeasonalComparison />

          <CarbonScenarios />

          {/* iter #41: Pareto sweet-spot finder (recommended carbon tax) */}
          <SweetSpot />
        </Suspense>
      )}

      {activeTab === 'network' && (
        <Suspense fallback={<LoadingSpinner label="Loading network…" />}>
          <FacilitiesList />
          {/* iter #23: DB export menu (csv / json / ndjson / parquet) */}
          <ExportButton />
        </Suspense>
      )}

      {activeTab === 'performance' && (
        <Suspense fallback={<LoadingSpinner label="Loading performance…" />}>
          {/* iter #22: API performance monitoring (perf middleware) */}
          <PerfStats />
          {/* iter #22: LLM token usage + cost tracking */}
          <LLMStats />
          {/* iter #28: LLM cost time-series chart (per sim_day) */}
          <LlmCostTimeseriesChart />
          {/* iter #29: LLM usage/cost forecast */}
          <LlmCostForecastPanel />
        </Suspense>
      )}

      {activeTab === 'history' && (
        <Suspense fallback={<LoadingSpinner label="Loading history…" />}>
          <CycleHistory />
        </Suspense>
      )}
    </div>
  )
}
