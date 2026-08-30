/**
 * LLMStats - LLM token usage + cost dashboard (iter #22 + iter #25 URL state)
 *
 * 数据源: GET /api/admin/llm-stats?recent=20
 *
 * 显示:
 * - 顶部 4 个 KPI: total_calls / total_tokens / total_cost_usd / error_rate
 * - by_caller 表格: caller / calls / prompt_tokens / candidate_tokens / total / cost / errors
 * - by_model 表格: model / calls / tokens / cost
 * - 最近 N 条 record: timestamp / caller / model / tokens / cost
 *
 * 自动 refresh 每 30s
 *
 * URL state (iter #25): ?llm_recent=50
 *
 * 用途: 监控 Gemini API 调用, 控制成本, 调试 LLM-driven 决策
 */

import { useState, useEffect } from 'react'
import { useUrlState } from '../../hooks/useUrlState'
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
} from 'recharts'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const REFRESH_INTERVAL_MS = 30000

const CALLER_COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16']

function formatTokens(n) {
  if (n == null) return '—'
  if (n < 1000) return `${n}`
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`
  return `${(n / 1_000_000).toFixed(2)}M`
}

function formatCost(usd) {
  if (usd == null) return '—'
  if (usd < 0.001) return `$${(usd * 1_000_000).toFixed(2)}µ`
  if (usd < 1) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

function formatTimestamp(ts) {
  if (ts == null) return '—'
  try {
    const d = new Date(ts * 1000)
    return d.toLocaleTimeString()
  } catch {
    return String(ts)
  }
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

export function LLMStats() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  // iter #25: URL-synced recent count
  const [recentN] = useUrlState('llm_recent', 20, 'int')

  const fetchStats = async () => {
    try {
      const resp = await fetch(`${API_BASE}/admin/llm-stats?recent=${recentN}`)
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`)
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
    fetchStats()
    const interval = setInterval(fetchStats, REFRESH_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [])

  if (loading) return <LoadingSpinner label="Loading LLM stats…" />
  if (error) {
    return (
      <div className="chart-card">
        <h3>🤖 LLM Usage</h3>
        <div className="error-banner">⚠️ {error}</div>
      </div>
    )
  }
  if (!data) return null

  const {
    total_calls = 0,
    total_errors = 0,
    error_rate_pct = 0,
    total_prompt_tokens = 0,
    total_candidate_tokens = 0,
    total_tokens = 0,
    total_cost_usd = 0,
    avg_tokens_per_call = 0,
    by_caller = {},
    by_model = {},
    recent = [],
  } = data

  // Pie chart: tokens by caller
  const callerPieData = Object.entries(by_caller)
    .map(([caller, stats]) => ({
      name: caller,
      value: stats.total_tokens || 0,
    }))
    .sort((a, b) => b.value - a.value)

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <h3>🤖 LLM Usage & Cost (iter #22)</h3>
        <span className="chart-card-sub">
          Token tracking via Gemini usage_metadata · Auto-refresh 30s
        </span>
      </div>

      <div className="kpi-grid">
        <KpiCard
          label="Total Calls"
          value={total_calls.toLocaleString()}
          unit=" calls"
          accent="#3b82f6"
        />
        <KpiCard
          label="Total Tokens"
          value={formatTokens(total_tokens)}
          unit={` (${formatTokens(avg_tokens_per_call)} avg)`}
          accent="#8b5cf6"
        />
        <KpiCard
          label="Total Cost"
          value={formatCost(total_cost_usd)}
          unit=" USD (est)"
          accent="#22c55e"
        />
        <KpiCard
          label="Error Rate"
          value={error_rate_pct.toFixed(1)}
          unit=" %"
          accent={error_rate_pct > 10 ? '#ef4444' : error_rate_pct > 2 ? '#f59e0b' : '#22c55e'}
        />
      </div>

      <div className="info-row" style={{ marginTop: '0.5rem' }}>
        <div className="info-item">
          <span className="info-label">Prompt:</span>{' '}
          <strong>{formatTokens(total_prompt_tokens)}</strong>
        </div>
        <div className="info-item">
          <span className="info-label">Candidate:</span>{' '}
          <strong>{formatTokens(total_candidate_tokens)}</strong>
        </div>
        <div className="info-item">
          <span className="info-label">Errors:</span>{' '}
          <strong style={{ color: total_errors > 0 ? '#ef4444' : '#94a3b8' }}>
            {total_errors}
          </strong>
        </div>
      </div>

      {total_calls === 0 ? (
        <div className="empty-state">
          🤖 No LLM calls tracked yet. Trigger an /api/optimize call and they'll appear here.
        </div>
      ) : (
        <>
          {/* Pie chart: tokens by caller */}
          {callerPieData.length > 0 && (
            <div className="chart-row" style={{ height: 240, marginTop: '1rem' }}>
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={callerPieData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    outerRadius={80}
                    label={(d) => `${d.name}: ${formatTokens(d.value)}`}
                    labelLine={false}
                  >
                    {callerPieData.map((_, i) => (
                      <Cell key={i} fill={CALLER_COLORS[i % CALLER_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{ background: '#1e293b', border: '1px solid #475569' }}
                    formatter={(v) => formatTokens(v)}
                  />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* By caller table */}
          {Object.keys(by_caller).length > 0 && (
            <table className="perf-table">
              <thead>
                <tr>
                  <th>Caller</th>
                  <th>Calls</th>
                  <th>Prompt Tok</th>
                  <th>Candidate Tok</th>
                  <th>Total Tok</th>
                  <th>Cost (USD)</th>
                  <th>Errors</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(by_caller)
                  .sort((a, b) => b[1].total_tokens - a[1].total_tokens)
                  .map(([caller, stats]) => (
                    <tr key={caller}>
                      <td>
                        <code className="perf-endpoint-name">{caller}</code>
                      </td>
                      <td>{stats.calls}</td>
                      <td>{formatTokens(stats.prompt_tokens)}</td>
                      <td>{formatTokens(stats.candidate_tokens)}</td>
                      <td><strong>{formatTokens(stats.total_tokens)}</strong></td>
                      <td>{formatCost(stats.cost_usd)}</td>
                      <td style={{ color: stats.errors > 0 ? '#ef4444' : '#94a3b8' }}>
                        {stats.errors}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}

          {/* By model table */}
          {Object.keys(by_model).length > 0 && (
            <table className="perf-table" style={{ marginTop: '1rem' }}>
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Calls</th>
                  <th>Total Tokens</th>
                  <th>Cost (USD)</th>
                  <th>Errors</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(by_model)
                  .sort((a, b) => b[1].cost_usd - a[1].cost_usd)
                  .map(([model, stats]) => (
                    <tr key={model}>
                      <td>
                        <code className="perf-endpoint-name">{model}</code>
                      </td>
                      <td>{stats.calls}</td>
                      <td><strong>{formatTokens(stats.total_tokens)}</strong></td>
                      <td>{formatCost(stats.cost_usd)}</td>
                      <td style={{ color: stats.errors > 0 ? '#ef4444' : '#94a3b8' }}>
                        {stats.errors}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}

          {/* Recent calls */}
          {recent.length > 0 && (
            <details style={{ marginTop: '1rem' }}>
              <summary style={{ cursor: 'pointer', color: '#94a3b8' }}>
                📋 Recent {recent.length} calls (click to expand)
              </summary>
              <table className="perf-table" style={{ marginTop: '0.5rem' }}>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Caller</th>
                    <th>Model</th>
                    <th>Tokens</th>
                    <th>Duration</th>
                    <th>Cost</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((rec, i) => (
                    <tr key={i}>
                      <td style={{ fontSize: '0.85em' }}>{formatTimestamp(rec.timestamp)}</td>
                      <td><code className="perf-endpoint-name">{rec.caller}</code></td>
                      <td style={{ fontSize: '0.85em' }}>{rec.model}</td>
                      <td>{formatTokens(rec.total_tokens)}</td>
                      <td>{formatMs(rec.duration_ms)}</td>
                      <td>{formatCost(rec.cost_usd)}</td>
                      <td>
                        {rec.success ? (
                          <span style={{ color: '#22c55e' }}>✓</span>
                        ) : (
                          <span style={{ color: '#ef4444' }} title={rec.error_type}>
                            ✗ {rec.error_type}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </>
      )}
    </div>
  )
}

function formatMs(ms) {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms.toFixed(0)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

export default LLMStats
