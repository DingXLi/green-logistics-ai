/**
 * HealthDashboardPanel - visualize /api/health/deep response (iter #60)
 *
 * 数据源:
 *   GET /api/health/deep[?include=database,simulation,weather,...]
 *
 * 显示:
 * - Overall status banner (ok / degraded / down) with color coding
 * - 9 subsystem cards (database, websocket, osm, scheduler, llm, agents,
 *   signals, simulation, weather), each with:
 *   - status badge (ok / degraded / down / idle)
 *   - 2-3 key metrics
 *   - expand button to show full details JSON
 * - Refresh button (manual) + auto-refresh every 30s
 * - Optional subsystem filter (multi-select dropdown)
 *
 * 用途: 运维 / 部署后验证 / 诊断子系统问题
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 30_000

const STATUS_COLORS = {
  ok: '#10b981',         // green
  degraded: '#f59e0b',   // amber
  down: '#ef4444',       // red
  idle: '#6b7280',       // gray
}

const STATUS_EMOJI = {
  ok: '✅',
  degraded: '⚠️',
  down: '❌',
  idle: '💤',
}

const SUBSYSTEM_ORDER = [
  'database', 'websocket', 'osm', 'scheduler',
  'llm', 'agents', 'signals', 'simulation', 'weather',
]

// Friendly labels for the 9 subsystems
const SUBSYSTEM_LABELS = {
  database: { label: 'Database', emoji: '🗄️', desc: 'SQLite + cycle data' },
  websocket: { label: 'WebSocket', emoji: '🔌', desc: 'Live dashboard updates' },
  osm: { label: 'OSM / OSRM', emoji: '🗺️', desc: 'Real road distances' },
  scheduler: { label: 'Scheduler', emoji: '⏰', desc: 'Auto-cycle background task' },
  llm: { label: 'LLM (Gemini)', emoji: '🤖', desc: 'AI predictions' },
  agents: { label: 'Agents', emoji: '🧑‍🌾', desc: 'Supply / demand / vehicle counts' },
  signals: { label: 'Eurostat', emoji: '📊', desc: 'External economic signals' },
  simulation: { label: 'Simulation', emoji: '⚙️', desc: 'Optimization cycles' },
  weather: { label: 'Weather (SMHI)', emoji: '🌤️', desc: 'Borås forecast cache' },
}

export function HealthDashboardPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [include, setInclude] = useState('')  // empty = all
  const [expanded, setExpanded] = useState(new Set())
  const [lastUpdated, setLastUpdated] = useState(null)

  const fetchHealth = async (inc = include) => {
    try {
      const url = new URL(`${API_BASE}/health/deep`)
      if (inc) url.searchParams.set('include', inc)
      const res = await fetch(url.toString())
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json)
      setError(null)
      setLastUpdated(new Date())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchHealth()
    const id = setInterval(() => fetchHealth(), REFRESH_MS)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    fetchHealth(include)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [include])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading health status…" />
  }

  if (error || !data) {
    return (
      <div className="card health-dashboard-panel">
        <h3>🩺 System Health (iter #60)</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No health data available.'}
        </div>
      </div>
    )
  }

  const overallStatus = data.status
  const checks = data.checks || {}
  const overallColor = STATUS_COLORS[overallStatus] || STATUS_COLORS.idle
  const nOk = Object.values(checks).filter(c => c?.status === 'ok').length
  const nDegraded = Object.values(checks).filter(c => c?.status === 'degraded').length
  const nDown = Object.values(checks).filter(c => c?.status === 'down').length
  const nIdle = Object.values(checks).filter(c => c?.status === 'idle').length

  const toggleExpand = (name) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  return (
    <div className="card health-dashboard-panel">
      <div className="card-header-row">
        <h3>🩺 System Health</h3>
        <div className="card-controls">
          <button
            className="refresh-btn"
            onClick={() => fetchHealth()}
            title="Refresh now"
          >
            🔄 Refresh
          </button>
        </div>
      </div>

      {/* Overall status banner */}
      <div
        className="health-overall-banner"
        style={{ background: `${overallColor}20`, borderColor: overallColor }}
      >
        <div className="health-overall-status">
          <span className="health-overall-emoji">
            {STATUS_EMOJI[overallStatus] || '❓'}
          </span>
          <span className="health-overall-label" style={{ color: overallColor }}>
            {overallStatus.toUpperCase()}
          </span>
          <span className="health-overall-counts">
            {nOk} ok · {nDegraded} degraded · {nDown} down · {nIdle} idle
          </span>
        </div>
        {lastUpdated && (
          <div className="health-overall-meta">
            Last check: {lastUpdated.toLocaleTimeString()} ·{' '}
            {data.n_subsystems || Object.keys(checks).length} subsystems
          </div>
        )}
      </div>

      {/* Filter dropdown */}
      <div className="filter-row">
        <label>Subsystems:</label>
        <select
          className="filter-select"
          value={include}
          onChange={(e) => setInclude(e.target.value)}
        >
          <option value="">— All (9) —</option>
          {SUBSYSTEM_ORDER.map((name) => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
        <span className="filter-hint">
          (Hold to filter, leave blank for all)
        </span>
      </div>

      {/* Subsystem grid */}
      <div className="health-subsystem-grid">
        {SUBSYSTEM_ORDER
          .filter((name) => checks[name] !== undefined)
          .map((name) => {
            const info = checks[name] || {}
            const status = info.status || 'unknown'
            const color = STATUS_COLORS[status] || STATUS_COLORS.idle
            const label = SUBSYSTEM_LABELS[name] || { label: name, emoji: '🔧', desc: '' }
            const isExpanded = expanded.has(name)

            // Pick 2-3 key metrics
            const keyMetrics = pickKeyMetrics(name, info)

            return (
              <div
                key={name}
                className="health-subsystem-card"
                style={{ borderColor: color }}
              >
                <div
                  className="health-subsystem-header"
                  onClick={() => toggleExpand(name)}
                >
                  <div className="health-subsystem-emoji">{label.emoji}</div>
                  <div className="health-subsystem-title">
                    <div className="health-subsystem-name">{label.label}</div>
                    <div className="health-subsystem-desc">{label.desc}</div>
                  </div>
                  <div
                    className="health-subsystem-status"
                    style={{ background: color }}
                  >
                    {STATUS_EMOJI[status] || '❓'} {status}
                  </div>
                </div>
                {/* Key metrics */}
                <div className="health-subsystem-metrics">
                  {keyMetrics.map((m, idx) => (
                    <div key={idx} className="health-metric">
                      <span className="health-metric-label">{m.label}:</span>
                      <span className="health-metric-value">{m.value}</span>
                    </div>
                  ))}
                </div>
                {/* Expand toggle */}
                <button
                  className="health-expand-btn"
                  onClick={() => toggleExpand(name)}
                >
                  {isExpanded ? '▼ Hide details' : '▶ Show details'}
                </button>
                {isExpanded && (
                  <pre className="health-details-json">
                    {JSON.stringify(info, null, 2)}
                  </pre>
                )}
              </div>
            )
          })}
      </div>

      <div className="card-footnote">
        iter #60 · auto-refresh 30s · 9 subsystems monitored
        (database, websocket, osm, scheduler, llm, agents, signals,
        simulation, weather)
      </div>
    </div>
  )
}

function pickKeyMetrics(name, info) {
  // Pick 2-3 most relevant metrics per subsystem
  switch (name) {
    case 'database':
      return [
        { label: 'cycles', value: info.n_cycles ?? '—' },
        { label: 'path', value: info.db_path ? info.db_path.split('/').pop() : '—' },
      ]
    case 'websocket':
      return [
        { label: 'clients', value: info.total_clients ?? 0 },
        { label: 'broadcasts', value: info.broadcasts_sent ?? 0 },
      ]
    case 'osm':
      return [
        { label: 'osmnx', value: info.osmnx_available ? 'available' : 'missing' },
      ]
    case 'scheduler':
      return [
        { label: 'cycles', value: info.cycle_count ?? 0 },
        { label: 'errors', value: info.error_count ?? 0 },
      ]
    case 'llm':
      return [
        { label: 'API key', value: info.api_key_set ? '✅ set' : '❌ missing' },
        { label: 'model', value: info.model || '—' },
      ]
    case 'agents':
      return [
        { label: 'supply', value: info.n_supply ?? 0 },
        { label: 'demand', value: info.n_demand ?? 0 },
        { label: 'vehicles', value: info.n_vehicles ?? 0 },
      ]
    case 'signals':
      return [
        { label: 'sources',
          value: `${info.n_indicators_ok ?? 0}/${info.n_indicators_total ?? 3}` },
        { label: 'eurostat',
          value: info.construction_source === 'eurostat' ? '✅ live' : 'cache' },
      ]
    case 'simulation':
      return [
        { label: 'last day', value: info.last_persisted_cycle?.sim_day ?? '—' },
        { label: 'batch',
          value: info.batch_tasks
            ? `${info.batch_tasks.n_completed}/${info.batch_tasks.n_total}`
            : '—' },
      ]
    case 'weather':
      return [
        { label: 'source', value: info.cache?.source || '—' },
        { label: 'age',
          value: info.cache?.age_seconds != null
            ? `${Math.round(info.cache.age_seconds)}s`
            : '—' },
      ]
    default:
      return []
  }
}
